import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from mcp import StdioServerParameters
from mcp.client import Client

pytestmark = pytest.mark.anyio


async def test_install_launch_reinstall_uninstall_preserves_settings(tmp_path):
    home = tmp_path / "home with spaces"
    config = home / "cortex"
    config.mkdir(parents=True)
    settings = config / "settings.json"
    original = {"theme": "dark", "plugins": ["other-plugin"], "custom": {"enabled": True}}
    settings.write_text(json.dumps(original), encoding="utf-8")
    env = {
        **os.environ,
        "SNOWFLAKE_HOME": str(home),
        "UV_PROJECT_ENVIRONMENT": str(tmp_path / "venv"),
    }
    for _ in range(2):
        await asyncio.to_thread(
            subprocess.run, [sys.executable, "-m", "colex", "install"], env=env, check=True
        )
    installed = json.loads(settings.read_text())
    assert installed == {**original, "plugins": ["other-plugin", str(config / "colex-plugin")]}
    plugin = Path(installed["plugins"][1])
    assert (plugin / "skills/executing-commands/SKILL.md").is_file()
    server = json.loads((plugin / ".mcp.json").read_text())["mcpServers"]["colex"]
    params = StdioServerParameters(command=server["command"], args=server["args"], env=env)
    async with Client(params) as session:
        result = await session.call_tool(
            "run_process",
            {
                "executable": sys.executable,
                "args": ["-c", "print('installed server works')"],
                "cwd": str(tmp_path),
            },
        )
        assert not result.is_error
        assert json.loads(result.content[0].text)["preview"].strip() == "installed server works"
    await asyncio.to_thread(
        subprocess.run, [sys.executable, "-m", "colex", "uninstall"], env=env, check=True
    )
    assert json.loads(settings.read_text()) == original
    assert settings.with_suffix(".json.bak").is_file()
