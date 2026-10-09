import base64
import json
import os
import shutil
from pathlib import Path

from mcp.server import MCPServer

from colex.runner import Runner

mcp = MCPServer("colex", log_level="WARNING")
_runner: Runner | None = None


def runner() -> Runner:
    global _runner
    if _runner is None:
        home = Path(os.environ.get("SNOWFLAKE_HOME", Path.home() / ".snowflake"))
        _runner = Runner(home / "cortex" / "colex-logs")
    return _runner


@mcp.tool(structured_output=False)
async def run_process(
    executable: str, args: list[str], cwd: str, timeout_seconds: float = 120
) -> str:
    """Run an executable directly, without a shell, in an absolute cwd.

    Noninteractive; default timeout 120s, maximum 600s. Returns exit status,
    a tail preview (16 KiB/200 lines), and a log ID for read_output. Stops at
    16 MiB of output. Runs with your permissions, not in a sandbox.
    """
    result = await runner().run(executable, args, cwd, timeout_seconds)
    return json.dumps(result, ensure_ascii=False)


@mcp.tool(structured_output=False)
async def run_powershell(
    cwd: str,
    script: str | None = None,
    script_path: str | None = None,
    args: list[str] | None = None,
    timeout_seconds: float = 120,
) -> str:
    """Run either inline PowerShell or an absolute .ps1 file, without profiles.

    Args apply only to script_path. Prefers pwsh; Windows can fall back to
    Windows PowerShell 5.1. Same timeout/output limits as run_process.
    No execution-policy bypass. No interactive input or persistent shell state.
    """
    if (script is None) == (script_path is None):
        raise ValueError("Provide exactly one of script or script_path")
    shell = shutil.which("pwsh")
    if not shell and os.name == "nt":
        shell = str(
            Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        )
        if not Path(shell).is_file():
            shell = None
    if not shell:
        raise ValueError("PowerShell is unavailable. Install approved PowerShell 7 (pwsh).")
    arguments = ["-NoLogo", "-NoProfile", "-NonInteractive"]
    if script_path is not None:
        path = Path(script_path)
        if not path.is_absolute() or not path.is_file() or path.suffix.lower() != ".ps1":
            raise ValueError("script_path must be an existing absolute .ps1 file")
        arguments += ["-File", str(path), *(args or [])]
    else:
        if args:
            raise ValueError("args can only be supplied with script_path")
        # Encoding avoids quoting script text through multiple command-line parsers.
        source = (
            "$ErrorActionPreference = 'Stop'; "
            "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
            "$OutputEncoding = [Console]::OutputEncoding; "
            + script
            + "\nif ($null -ne $LASTEXITCODE) { exit $LASTEXITCODE }"
        )
        encoded = base64.b64encode(source.encode("utf-16-le")).decode("ascii")
        arguments += ["-EncodedCommand", encoded]
    result = await runner().run(shell, arguments, cwd, timeout_seconds)
    result["powershell"] = shell
    return json.dumps(result, ensure_ascii=False)


@mcp.tool(structured_output=False)
def read_output(log_id: str, offset: int = 0, max_bytes: int = 16384) -> str:
    """Read a bounded page of a command log. Use next_offset to continue.

    Offsets are bytes. Maximum page is 16 KiB. Only Colex log IDs are accepted,
    not arbitrary paths. Do not read entire large logs into context by default.
    """
    return json.dumps(runner().read_output(log_id, offset, max_bytes), ensure_ascii=False)
