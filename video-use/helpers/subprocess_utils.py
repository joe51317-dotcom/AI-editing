"""Consistent, secret-safe subprocess handling for video-use helpers."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Iterable, Sequence


SECRET_PATTERNS = (
    re.compile(r"(?i)(HF_TOKEN|API_KEY|TOKEN)=([^\s]+)"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"),
)


class CommandError(RuntimeError):
    pass


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    result = text
    for secret in secrets:
        if secret:
            result = result.replace(secret, "<redacted>")
    for pattern in SECRET_PATTERNS:
        result = pattern.sub(lambda m: f"{m.group(1)}<redacted>", result)
    return result


def run_checked(
    command: Sequence[str | os.PathLike[str]],
    *,
    stage: str,
    env: dict[str, str] | None = None,
    secrets: Iterable[str] = (),
    capture_output: bool = True,
    text: bool = True,
) -> subprocess.CompletedProcess:
    cmd = [str(part) for part in command]
    try:
        return subprocess.run(
            cmd,
            check=True,
            capture_output=capture_output,
            text=text,
            env=env,
        )
    except FileNotFoundError as exc:
        raise CommandError(f"{stage} failed: executable not found: {cmd[0]}") from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr or ""
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        stderr_tail = "\n".join(stderr.strip().splitlines()[-30:])
        safe_cmd = redact(" ".join(cmd), secrets)
        safe_stderr = redact(stderr_tail, secrets)
        detail = safe_stderr or "no stderr output"
        raise CommandError(
            f"{stage} failed (exit {exc.returncode}).\nCommand: {safe_cmd}\n{detail}"
        ) from exc
