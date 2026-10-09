# Colex

A personal [Snowflake CoCo](https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code-plugins)
plugin with a Python MCP server and a skill for native CLI commands and PowerShell.
No Bash, Node, HTTP listener, or custom executable is required on Windows.
Python and dependencies are managed exclusively through **uv** and `uv.lock`.

## Install

Prerequisites: Git, an approved installation of [uv](https://docs.astral.sh/uv/getting-started/installation/),
and CoCo CLI with plugin support. PowerShell 7 (`pwsh`) is recommended for scripts;
Windows PowerShell is a fallback on Windows. Neither is needed for direct CLI execution.

Run these commands in PowerShell on Windows, or your terminal on macOS/Linux:

```console
git clone https://github.com/curtisalexander/colex.git
uv run --locked --no-dev --directory colex colex install
```

uv creates an isolated environment and can download a compatible Python if needed.
If company policy requires an existing approved Python, use
`uv run --no-python-downloads --python <approved-python-path> --locked --no-dev --directory colex colex install`.
Use your organization's approved package index/proxy settings where required.
Do not use pip, global Python package installs, or an unpinned `uvx` command.

The installer copies the plugin manifest and skill to
`~/.snowflake/cortex/colex-plugin/`, generates `.mcp.json` with absolute uv/checkout
paths, and adds that directory to `settings.json`'s `plugins` array. It preserves
other settings and backs up the previous settings to `settings.json.bak`.
`SNOWFLAKE_HOME` overrides `~/.snowflake`. Close CoCo settings editors while installing.
Keep the checkout in place; moving it requires rerunning the installer.

Restart CoCo or run `/plugin reload`, then check `/mcp` and `/skill list`.
Try: **Use executing-commands to run git status in my project through Colex.**

Use this installer rather than `cortex plugin install owner/repo`: generating
absolute local paths avoids relying on undocumented plugin-root substitutions.
The repository manifest alone contributes the skill; the generated installed
plugin includes the MCP registration as well.

### Update and uninstall

```console
git -C colex pull --ff-only
uv run --locked --no-dev --directory colex colex install
```

Restart CoCo after updates. To unregister:

```console
uv run --locked --no-dev --directory colex colex uninstall
```

Uninstall leaves the checkout, generated plugin files, and logs intact. It does not
remove other plugins, change permissions, or touch Snowflake connections.

## Tools

| Tool | Purpose |
| --- | --- |
| `run_process(executable, args, cwd, timeout_seconds=120)` | Direct process execution, no shell interpretation |
| `run_powershell(cwd, script=..., script_path=..., args=..., timeout_seconds=120)` | Inline PowerShell **or** a `.ps1` file; args are for files only |
| `read_output(log_id, offset=0, max_bytes=16384)` | Bounded log pages with a byte continuation offset |

CoCo names these `mcp__colex__run_process`, etc. Pass an absolute working directory.
Arguments to `run_process` are literal strings, not a quoted shell command.
Windows `.cmd`/`.bat` wrappers require `run_powershell` (for example `& npm.cmd test`).
Commands receive the server's environment, not your interactive PowerShell profile.
Use explicit executable paths for project virtual environments.

PowerShell launches with `-NoLogo -NoProfile -NonInteractive`, with no execution-policy
bypass. Inline code uses `-EncodedCommand` to avoid quoting errors; encoding is not
encryption, and corporate policy can still block it. Inline code sets UTF-8 output
and stops on PowerShell errors; a final native command's exit code is propagated.
Script files use `-File` and their own exit/error behavior. Windows PowerShell 5.1
scripts and older native programs may need explicit UTF-8 output configuration;
non-UTF-8 output is decoded with replacement characters. Logs preserve original bytes.

## Deliberately small protections

- Noninteractive commands with a **120-second default / 600-second maximum** timeout.
- **16 KiB / 200-line tail preview**, plus bounded log reads. JSON metadata/escaping
  adds overhead; these are output bounds, not exact model token counts.
- Output streams to disk and a bounded memory buffer. **16 MiB per command** is a
  hard stop: the process is terminated and the result says the log is incomplete.
- stdout and stderr share a log; no duplicate structured/text MCP results.
- Logs live in `~/.snowflake/cortex/colex-logs/`. Files older than seven days are
  removed at server startup. There is no global disk quota or background cleanup.
- Timeout/cancellation attempts to kill the process tree. Unix uses process groups;
  Windows uses psutil descendant enumeration. This is best-effort cleanup, not
  containment: detached/reparented processes can escape it.

**Not a sandbox.** Commands have your account's filesystem, network, and inherited
credential access. Keep CoCo approvals enabled; the installer does not auto-approve
tools. No regex command denylist, LLM safety checker, or policy engine is included.
Logs may contain secrets printed by commands: keep the config directory private.
On Windows, files inherit its ACLs; Colex does not reconfigure corporate permissions.

No hooks, persistent shell sessions, background-job service, or live terminal-output
streaming in this first version. The MCP process stays alive between tool calls.

## Why the official MCP SDK?

Colex uses **`mcp==2.3.0`**, the official Python SDK, with its high-level `MCPServer`
API. We do not implement the wire protocol ourselves. Its compatibility support
allows older handshake-based clients as well as newer protocol clients.

Alternatives measured in clean uv environments (Linux x64, Python 3.11):

| Package | Installed distributions | Package files, excluding bytecode | Import + construct median | Warm stdio echo median |
| --- | ---: | ---: | ---: | ---: |
| Official `mcp==1.30.0` | 29 | 28.3 MiB | 454 ms | 2.93 ms |
| Official `mcp==2.3.0` | 28 | 28.7 MiB | 731 ms | 2.00 ms |
| Prefect `fastmcp==4.1.0` | 71 | 55.1 MiB | 872 ms | 2.47 ms |

These are local microbenchmarks, not Windows performance guarantees. Startup:
nine fresh processes after warmup, no uv startup/download time. Calls: 100 echo
calls after five warmups using the same official v1 stdio client for all servers.
Sizes include dependencies but exclude Python, uv, and caches. These measurements
do not measure memory consumption or real command latency.

Both projects publish recent releases. Official v1 remains maintained, but v2 is
the current line with essentially the same footprint. Standalone FastMCP offers
more framework conveniences but adds dependencies we do not need. Its bare slim
package cannot run a server; `fastmcp-slim[server]==4.1.0` resolved to 70 packages.
Writing our own protocol would save dependencies but create ongoing compatibility,
validation, and cancellation maintenance. It is not worth that tradeoff here.

## Development

```console
uv sync --locked
uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
```

Tests launch real stdio server subprocesses, including the uv-generated install
configuration. PowerShell tests run when `pwsh` or Windows PowerShell is available.
CI declares Windows/macOS/Linux coverage. Local Linux tests do not establish Windows
or corporate CoCo compatibility; verify `/mcp` on the target machine before relying
on it. Administrators can disable user MCP servers even when Python is permitted.
