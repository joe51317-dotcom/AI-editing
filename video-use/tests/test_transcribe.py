from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import transcribe
from config import VideoUseConfig
from transcribe import TranscriptionOptions


def fake_config(tmp_path: Path) -> VideoUseConfig:
    return VideoUseConfig(
        repo_root=tmp_path,
        asr_python=Path("python.exe"),
        model="large-v3",
        device="cuda",
        compute_type="float16",
        font_path=None,
        hf_token="token-value",
        env={},
    )


def install_fake_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    stages: list[str] = []
    monkeypatch.setattr(transcribe, "load_config", lambda: fake_config(tmp_path))
    monkeypatch.setattr(transcribe, "run_doctor", lambda **kwargs: {"ok": True, "errors": []})
    monkeypatch.setattr(transcribe, "_probe_duration", lambda path: 5.0)

    def fake_run(command, *, stage, **kwargs):
        stages.append(stage)
        command = [str(item) for item in command]
        if "asr" in command:
            spec = json.loads(Path(command[-1]).read_text(encoding="utf-8"))
            for job in spec["jobs"]:
                Path(job["output"]).write_text(
                    json.dumps(
                        {
                            "language": "zh", "language_probability": 0.99,
                            "words": [{"word": "你好", "start": 0.1, "end": 0.6, "probability": 0.9}],
                        }
                    ),
                    encoding="utf-8",
                )
        elif "diarize" in command:
            spec = json.loads(Path(command[-1]).read_text(encoding="utf-8"))
            for job in spec["jobs"]:
                Path(job["output"]).write_text(
                    json.dumps({"speaker_turns": [{"start": 0, "end": 1, "speaker_id": "SPEAKER_00"}]}),
                    encoding="utf-8",
                )
        elif command[0] == "ffmpeg":
            Path(command[-1]).write_bytes(b"wav")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(transcribe, "run_checked", fake_run)
    return stages


def test_num_speakers_requires_diarization(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="requires"):
        transcribe.transcribe_many([], tmp_path, TranscriptionOptions(num_speakers=2))


def test_low_memory_is_explicit() -> None:
    config = fake_config(Path("."))
    settings = transcribe._effective_settings(config, TranscriptionOptions(low_memory=True))
    assert settings["compute_type"] == "int8_float16"
    assert settings["model"] == "large-v3"


def test_batch_loads_asr_once_and_writes_v2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stages = install_fake_runtime(monkeypatch, tmp_path)
    videos = [tmp_path / "a.mp4", tmp_path / "b.mp4"]
    for video in videos:
        video.write_bytes(b"media")
    outputs = transcribe.transcribe_many(videos, tmp_path / "edit", TranscriptionOptions(language="zh"), verbose=False)
    assert len(outputs) == 2
    assert stages.count("local faster-whisper ASR") == 1
    assert all(json.loads(path.read_text())["schema_version"] == 2 for path in outputs)


def test_diarization_starts_after_asr_and_assigns_speaker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stages = install_fake_runtime(monkeypatch, tmp_path)
    video = tmp_path / "talk.mp4"
    video.write_bytes(b"media")
    [output] = transcribe.transcribe_many(
        [video], tmp_path / "edit", TranscriptionOptions(diarize=True, num_speakers=2), verbose=False
    )
    assert stages.index("local faster-whisper ASR") < stages.index("local pyannote diarization")
    assert json.loads(output.read_text())["words"][0]["speaker_id"] == "SPEAKER_00"


def test_valid_cache_skips_asr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stages = install_fake_runtime(monkeypatch, tmp_path)
    video = tmp_path / "cached.mp4"
    video.write_bytes(b"media")
    edit = tmp_path / "edit"
    options = TranscriptionOptions(language="zh")
    transcribe.transcribe_many([video], edit, options, verbose=False)
    stages.clear()
    transcribe.transcribe_many([video], edit, options, verbose=False)
    assert "local faster-whisper ASR" not in stages


def test_sync_manifest_transcribes_aligned_audio_and_invalidates_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_runtime(monkeypatch, tmp_path)
    video = tmp_path / "take.mp4"
    aligned = tmp_path / "edit" / "sync" / "audio" / "take.wav"
    video.write_bytes(b"video")
    aligned.parent.mkdir(parents=True)
    aligned.write_bytes(b"aligned-v1")

    def signature(path: Path) -> dict:
        stat = path.stat()
        return {
            "path": str(path.resolve()), "size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns,
            "quick_sha256": transcribe.source_signature(path, 5.0)["quick_sha256"],
        }

    alignment = {
        "backend": "pluraleyes-xml", "audio_offset_s": 1.0,
        "coverage": {"video_start_s": 0, "video_end_s": 5, "audio_start_s": 1, "audio_end_s": 6},
        "gap_policy": "silence", "video": signature(video), "audio": signature(aligned),
        "aligned_audio": signature(aligned), "confidence": None,
    }
    manifest = {"schema_version": 1, "gap_policy": "silence", "sources": {"take": {
        "source_id": "take", "alignment": alignment, "materialized_path": None,
    }}}
    manifest_path = tmp_path / "edit" / "sync_manifest.json"
    manifest_path.parent.mkdir(exist_ok=True)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(transcribe, "load_sync_manifest", lambda path: manifest)
    monkeypatch.setattr(transcribe, "find_manifest_entry", lambda data, path: data["sources"]["take"])

    captured_inputs: list[str] = []
    original_run = transcribe.run_checked

    def capture_run(command, **kwargs):
        command_strings = [str(value) for value in command]
        if "asr" in command_strings:
            jobs = json.loads(Path(command_strings[-1]).read_text())
            captured_inputs.extend(job["input"] for job in jobs["jobs"])
        return original_run(command, **kwargs)

    monkeypatch.setattr(transcribe, "run_checked", capture_run)
    [output] = transcribe.transcribe_many(
        [video], tmp_path / "edit", TranscriptionOptions(sync_manifest=manifest_path), verbose=False
    )
    data = json.loads(output.read_text())
    assert captured_inputs == [str(aligned)]
    assert data["audio_alignment"]["gap_policy"] == "silence"
    assert data["transcription"]["audio_alignment_sha256"]


@pytest.mark.parametrize(("payload", "label"), [({"words": []}, "legacy"), ("{bad", "invalid")])
def test_old_cache_is_backed_up_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: object, label: str
) -> None:
    install_fake_runtime(monkeypatch, tmp_path)
    video = tmp_path / "old.mp4"
    video.write_bytes(b"media")
    transcripts = tmp_path / "edit" / "transcripts"
    transcripts.mkdir(parents=True)
    current = transcripts / "old.json"
    if isinstance(payload, str):
        current.write_text(payload, encoding="utf-8")
    else:
        current.write_text(json.dumps(payload), encoding="utf-8")
    transcribe.transcribe_many([video], tmp_path / "edit", TranscriptionOptions(), verbose=False)
    assert len(list(transcripts.glob(f"old.{label}.*.json"))) == 1
    assert json.loads(current.read_text())["schema_version"] == 2
