from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

import config
from subprocess_utils import CommandError, redact, run_checked


def test_env_file_and_process_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("VIDEO_USE_DEVICE=cpu\nVIDEO_USE_ASR_MODEL=local\nHF_TOKEN=secret\n", encoding="utf-8")
    monkeypatch.setenv("VIDEO_USE_DEVICE", "cuda")
    loaded = config.load_config(env_file)
    assert loaded.device == "cuda"
    assert loaded.model == "local"
    assert loaded.hf_token == "secret"


def test_redaction_covers_explicit_and_named_tokens() -> None:
    text = "HF_TOKEN=abc Bearer xyz CUSTOM=verysecret"
    safe = redact(text, ("verysecret",))
    assert "abc" not in safe and "xyz" not in safe and "verysecret" not in safe


def test_run_checked_success() -> None:
    result = run_checked([sys.executable, "-c", "print('ok')"], stage="unit")
    assert result.stdout.strip() == "ok"


def test_run_checked_failure_reports_stage_exit_and_tail() -> None:
    command = [sys.executable, "-c", "import sys; print('HF_TOKEN=secret', file=sys.stderr); sys.exit(7)"]
    with pytest.raises(CommandError) as raised:
        run_checked(command, stage="unit", secrets=("secret",))
    message = str(raised.value)
    assert "unit failed (exit 7)" in message
    assert "secret" not in message


def test_run_checked_missing_executable() -> None:
    with pytest.raises(CommandError, match="executable not found"):
        run_checked(["definitely-not-a-real-video-use-command"], stage="unit")
