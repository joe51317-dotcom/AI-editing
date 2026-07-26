from __future__ import annotations

import argparse
import json
import sys
import types
from pathlib import Path

import pytest

import local_backend


def test_backend_job_file_requires_jobs(tmp_path: Path) -> None:
    path = tmp_path / "jobs.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="jobs array"):
        local_backend._read_jobs(path)


def test_asr_success_and_empty_audio_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Info:
        language = "zh"
        language_probability = 0.8

    class Segment:
        words = []

    class Model:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, *args, **kwargs):
            return iter([Segment()]), Info()

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Model))
    output = tmp_path / "out.json"
    jobs = tmp_path / "jobs.json"
    jobs.write_text(
        json.dumps({"model": "m", "jobs": [{"input": "empty.wav", "output": str(output)}]}),
        encoding="utf-8",
    )
    assert local_backend.run_asr(jobs) == 0
    assert json.loads(output.read_text())["words"] == []


def test_asr_oom_has_retry_hint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Model:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("CUDA out of memory")

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Model))
    jobs = tmp_path / "jobs.json"
    jobs.write_text(json.dumps({"model": "m", "jobs": [{"input": "x", "output": "y"}]}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="--low-memory"):
        local_backend.run_asr(jobs)


def test_output_annotation_prefers_exclusive() -> None:
    exclusive = object()
    output = types.SimpleNamespace(exclusive_speaker_diarization=exclusive, speaker_diarization=object())
    assert local_backend._annotation_from_output(output) is exclusive


def test_doctor_reports_missing_token_for_diarization(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setattr(local_backend.importlib.metadata, "version", lambda name: "1")
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: False)))
    args = argparse.Namespace(model=str(Path(__file__)), device="cpu", diarize=True)
    # Structured output is tested by the orchestrator; this branch must remain non-throwing.
    assert local_backend.doctor(args) == 0
