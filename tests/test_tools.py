import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

import anyio
import psutil
import pytest
from mcp import StdioServerParameters
from mcp.client import Client

pytestmark = pytest.mark.anyio


@pytest.fixture
async def client(tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "colex", "serve"],
        env={**os.environ, "SNOWFLAKE_HOME": str(tmp_path / "snowflake")},
    )
    async with Client(params) as session:
        yield session


async def call(client, tool, **arguments):
    result = await client.call_tool(tool, arguments)
    assert not result.is_error, result.content
    assert result.structured_content is None  # Do not double-inject output into context.
    return json.loads(result.content[0].text)


async def python(client, tmp_path, source, **options):
    return await call(
        client,
        "run_process",
        executable=sys.executable,
        args=["-c", source],
        cwd=str(tmp_path),
        **options,
    )


async def test_stdio_discovery_arguments_cwd_and_nonzero_exit(client, tmp_path):
    tools = await client.list_tools()
    assert {t.name for t in tools.tools} == {"run_process", "run_powershell", "read_output"}
    arguments = ["space here", '"quoted"', "雪", "; echo NO", "$(whoami)", "a&b"]
    result = await call(
        client,
        "run_process",
        executable=sys.executable,
        args=[
            "-c",
            "import os,sys,json; print(json.dumps([os.getcwd(),sys.argv[1:]], ensure_ascii=False),flush=True); print('error marker',file=sys.stderr); sys.exit(7)",
            *arguments,
        ],
        cwd=str(tmp_path),
    )
    lines = result["preview"].splitlines()
    assert json.loads(lines[0]) == [str(tmp_path), arguments]
    assert lines[1] == "error marker"
    assert result["exit_code"] == 7
    assert result["log_complete"]


async def test_tail_limit_and_log_paging_preserve_unicode(client, tmp_path):
    result = await python(client, tmp_path, "print('α雪😀' * 8000, end='')")
    assert result["exit_code"] == 0
    assert result["preview_truncated"]
    assert len(result["preview"].encode()) <= 16384
    assert "�" not in result["preview"]
    offset = 0
    chunks = []
    while True:
        page = await call(
            client, "read_output", log_id=result["log_id"], offset=offset, max_bytes=1001
        )
        chunks.append(page["text"])
        assert page["next_offset"] > offset
        offset = page["next_offset"]
        if page["eof"]:
            break
    assert "".join(chunks) == "α雪😀" * 8000
    assert offset == result["captured_bytes"] == 72000


async def test_line_limit(client, tmp_path):
    result = await python(client, tmp_path, "print('\\n'.join(str(i) for i in range(350)))")
    assert result["preview"].splitlines() == [str(i) for i in range(150, 350)]
    assert result["preview_truncated"]


async def test_output_cap_stops_command(client, tmp_path):
    result = await python(client, tmp_path, "import os;\nwhile True: os.write(1,b'x'*8192)")
    assert result["output_limit_reached"]
    assert not result["log_complete"]
    assert result["captured_bytes"] == 16 * 1024 * 1024
    assert Path(result["log_path"]).stat().st_size == result["captured_bytes"]


async def test_timeout_kills_child_and_preserves_output(client, tmp_path):
    result = await python(
        client,
        tmp_path,
        "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); print(p.pid,flush=True); time.sleep(60)",
        timeout_seconds=2,
    )
    assert result["timed_out"]
    assert not result["log_complete"]
    pid = int(result["preview"].strip())
    for _ in range(40):
        if not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail(f"Child {pid} survived timeout")


async def test_invalid_requests_are_tool_errors(client, tmp_path):
    for tool, arguments in [
        ("run_process", {"executable": sys.executable, "args": [], "cwd": "."}),
        (
            "run_process",
            {
                "executable": sys.executable,
                "args": [],
                "cwd": str(tmp_path),
                "timeout_seconds": 601,
            },
        ),
        ("read_output", {"log_id": "../../LICENSE"}),
        ("run_powershell", {"cwd": str(tmp_path), "script": "1", "script_path": "bad.ps1"}),
    ]:
        result = await client.call_tool(tool, arguments)
        assert result.is_error


async def test_cancellation_cleans_up_and_server_remains_usable(client, tmp_path):
    pid_file = tmp_path / "running.pid"
    with anyio.move_on_after(2) as scope:
        await python(
            client,
            tmp_path,
            "import os,time; from pathlib import Path; Path('running.pid').write_text(str(os.getpid())); time.sleep(60)",
        )
    assert scope.cancel_called
    pid = int(pid_file.read_text())
    for _ in range(100):
        if not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail(f"Command {pid} survived cancellation")
    result = await python(client, tmp_path, "print('still usable')")
    assert result["preview"].strip() == "still usable"


@pytest.mark.skipif(not shutil.which("pwsh") and os.name != "nt", reason="PowerShell not installed")
async def test_powershell_inline_script_file_and_errors(client, tmp_path):
    result = await call(client, "run_powershell", cwd=str(tmp_path), script="'hello 雪'; 2 + 3")
    assert result["exit_code"] == 0
    assert result["preview"].splitlines() == ["hello 雪", "5"]
    script = tmp_path / "script with spaces.ps1"
    script.write_text("param([string]$value)\nWrite-Output $value\nexit 9\n", encoding="utf-8")
    result = await call(
        client, "run_powershell", cwd=str(tmp_path), script_path=str(script), args=["a b; 'quoted'"]
    )
    assert result["exit_code"] == 9
    assert result["preview"].strip() == "a b; 'quoted'"
    result = await call(
        client, "run_powershell", cwd=str(tmp_path), script="Write-Error 'expected failure'"
    )
    assert result["exit_code"] != 0
    assert "expected failure" in result["preview"]
