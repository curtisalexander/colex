"""Bounded, noninteractive subprocess execution. No shell or policy engine."""

import asyncio
import codecs
import os
import re
import shutil
import signal
import subprocess
import time
import uuid
from pathlib import Path

import anyio
import psutil

PREVIEW_BYTES = 16 * 1024
PREVIEW_LINES = 200
LOG_BYTES = 16 * 1024 * 1024
LOG_TTL = 7 * 24 * 60 * 60
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class Runner:
    def __init__(self, log_dir: Path):
        self.log_dir = log_dir
        log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        for path in log_dir.glob("*.log"):
            if path.stat().st_mtime < time.time() - LOG_TTL:
                path.unlink(missing_ok=True)

    async def run(
        self, executable: str, args: list[str], cwd: str, timeout_seconds: float = 120
    ) -> dict:
        directory = Path(cwd)
        if not directory.is_absolute() or not directory.is_dir():
            raise ValueError("cwd must be an existing absolute directory")
        if not 0 < timeout_seconds <= 600:
            raise ValueError("timeout_seconds must be greater than 0 and at most 600")
        # Resolve before spawning; Windows batch files can otherwise invoke a shell implicitly.
        candidate = Path(executable)
        if candidate.is_absolute():
            resolved = str(candidate)
        elif os.path.dirname(executable):
            resolved = str(directory / candidate)
        else:
            resolved = shutil.which(executable)
        if not resolved:
            raise ValueError(f"Executable not found: {executable}")
        if os.name == "nt" and Path(resolved).suffix.lower() in {".cmd", ".bat"}:
            raise ValueError("Use run_powershell for .cmd/.bat files; run_process uses no shell")

        log_id = uuid.uuid4().hex
        path = self.log_dir / f"{log_id}.log"
        tail = bytearray()
        captured = 0
        observed = 0
        timed_out = False
        output_limit = False
        started = time.monotonic()
        env = os.environ.copy()
        env.update(PYTHONIOENCODING="utf-8", NO_COLOR="1")
        options = (
            {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
            if os.name == "nt"
            else {"start_new_session": True}
        )
        with path.open("xb") as log:
            if os.name != "nt":
                path.chmod(0o600)
            proc = await asyncio.create_subprocess_exec(
                resolved,
                *args,
                cwd=directory,
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                **options,
            )

            async def collect() -> None:
                nonlocal captured, observed, output_limit
                while chunk := await proc.stdout.read(8192):
                    observed += len(chunk)
                    kept = chunk[: LOG_BYTES - captured]
                    log.write(kept)
                    captured += len(kept)
                    tail.extend(kept)
                    del tail[:-PREVIEW_BYTES]
                    if observed > LOG_BYTES:
                        output_limit = True
                        return
                await proc.wait()

            try:
                await asyncio.wait_for(collect(), timeout_seconds)
            except TimeoutError:
                timed_out = True
            finally:
                # Also end descendants after normal exit: tools are not a service launcher.
                self._kill_tree(proc.pid)
                with anyio.CancelScope(shield=True):

                    async def drain() -> None:
                        while await proc.stdout.read(8192):
                            pass
                        await proc.wait()

                    try:
                        await asyncio.wait_for(drain(), 5)
                    except TimeoutError:
                        pass

        if captured > PREVIEW_BYTES:
            while tail and tail[0] & 0xC0 == 0x80:
                del tail[0]
        text = ANSI.sub("", bytes(tail).decode("utf-8", errors="replace"))
        text = text.encode("utf-8")[-PREVIEW_BYTES:].decode("utf-8", errors="ignore")
        lines = text.splitlines(keepends=True)
        preview = "".join(lines[-PREVIEW_LINES:])
        return {
            "exit_code": proc.returncode,
            "timed_out": timed_out,
            "output_limit_reached": output_limit,
            "duration_seconds": round(time.monotonic() - started, 3),
            "preview": preview,
            "preview_truncated": captured > PREVIEW_BYTES or len(lines) > PREVIEW_LINES,
            "captured_bytes": captured,
            "log_complete": not (timed_out or output_limit),
            "log_id": log_id,
            "log_path": str(path),
        }

    @staticmethod
    def _kill_tree(pid: int) -> None:
        if os.name != "nt":
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            return
        try:
            parent = psutil.Process(pid)
            children = parent.children(recursive=True)
        except psutil.NoSuchProcess:
            return
        for process in [*reversed(children), parent]:
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass

    def read_output(self, log_id: str, offset: int = 0, max_bytes: int = PREVIEW_BYTES) -> dict:
        if not re.fullmatch(r"[0-9a-f]{32}", log_id):
            raise ValueError("Invalid log ID; use the ID returned by a command")
        if offset < 0 or not 1 <= max_bytes <= PREVIEW_BYTES:
            raise ValueError("offset must be nonnegative; max_bytes must be 1..16384")
        path = self.log_dir / f"{log_id}.log"
        with path.open("rb") as log:
            size = os.fstat(log.fileno()).st_size
            if offset > size:
                raise ValueError("offset is past the end of the log")
            log.seek(offset)
            data = log.read(max_bytes)
        data = b"".join(data.splitlines(keepends=True)[:PREVIEW_LINES])
        # Keep incomplete UTF-8 sequences for the next page (unless at EOF).
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        text = decoder.decode(data, final=offset + len(data) == size)
        pending = len(decoder.getstate()[0])
        consumed = len(data) - pending
        if data and consumed == 0:
            raise ValueError("max_bytes is too small for the next character; use at least 4")
        return {
            "text": ANSI.sub("", text),
            "next_offset": offset + consumed,
            "eof": offset + consumed == size,
        }
