"""Running a command-line AI (Claude Code or Codex) once, and reading what it prints.

The prompt goes in on stdin. The CLI prints one JSON event per line while it works,
and run_cli() yields those events as they arrive. It runs in an empty temporary
folder, so there is nothing next to it to read: its only way to the course material
is our tools, over MCP.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections import deque
from collections.abc import Iterator
from pathlib import Path

from .base import ProviderError


def run_cli(program: str, args: list[str], prompt: str, *, files: dict[str, str] | None = None,
            env: dict[str, str] | None = None) -> Iterator[dict]:
    """Run `program args` in an empty folder (plus `files`), send `prompt` on stdin, and
    yield each JSON line it prints. `env` adds environment variables."""
    exe = shutil.which(program)
    if exe is None:
        raise ProviderError(f"`{program}` was not found. Install it (and log in), or choose another AI.")
    recent: deque[str] = deque(maxlen=5)  # the last lines that were not JSON, for error messages

    with tempfile.TemporaryDirectory() as folder:
        for name, content in (files or {}).items():
            Path(folder, name).write_text(content, encoding="utf-8")
        proc = subprocess.Popen(
            [exe, *args], cwd=folder, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", env={**os.environ, **(env or {})},
        )
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
            for line in proc.stdout:
                line = line.strip()
                if line.startswith("{"):
                    try:
                        yield json.loads(line)
                        continue
                    except json.JSONDecodeError:
                        pass
                if line:
                    recent.append(line)
            proc.wait(timeout=30)
        finally:
            if proc.poll() is None:  # the page was closed mid-answer: stop the CLI
                proc.kill()
                proc.wait()

    if proc.returncode:
        raise ProviderError(f"`{program}` failed: {' | '.join(recent) or f'exit code {proc.returncode}'}")
