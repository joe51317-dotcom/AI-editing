"""Fail-fast environment validation for the local video-use fork."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from config import configure_console, load_config, resolve_font_path, validate_cjk_font
from subprocess_utils import CommandError, run_checked


def run_doctor(*, diarize: bool = False, model: str | None = None, device: str | None = None) -> dict:
    config = load_config()
    errors: list[str] = []
    for executable in ("ffmpeg", "ffprobe"):
        if not shutil.which(executable):
            errors.append(f"{executable} is not available on PATH")

    try:
        font = resolve_font_path(config)
        validate_cjk_font(font)
    except Exception as exc:
        font = None
        errors.append(str(exc))

    if config.asr_python is None:
        errors.append("VIDEO_USE_ASR_PYTHON is missing from video-use/.env")
        backend = None
    elif not config.asr_python.is_file():
        errors.append(f"VIDEO_USE_ASR_PYTHON does not exist: {config.asr_python}")
        backend = None
    else:
        command = [
            config.asr_python,
            Path(__file__).with_name("local_backend.py"),
            "doctor",
            "--model",
            model or config.model,
            "--device",
            device or config.device,
        ]
        if diarize:
            command.append("--diarize")
        try:
            result = run_checked(
                command,
                stage="DACE runtime preflight",
                env=config.child_env(),
                secrets=(config.hf_token,),
            )
            backend = json.loads(result.stdout.strip().splitlines()[-1])
            errors.extend(backend.get("errors", []))
        except (CommandError, json.JSONDecodeError) as exc:
            backend = None
            errors.append(str(exc))

    return {
        "ok": not errors,
        "font": str(font) if font else None,
        "backend": backend,
        "errors": errors,
    }


def main() -> int:
    configure_console()
    parser = argparse.ArgumentParser(description="Validate video-use local dependencies")
    parser.add_argument("--diarize", action="store_true")
    parser.add_argument("--model")
    parser.add_argument("--device", choices=("cuda", "cpu"))
    args = parser.parse_args()
    report = run_doctor(diarize=args.diarize, model=args.model, device=args.device)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
