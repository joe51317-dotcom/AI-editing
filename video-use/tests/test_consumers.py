from __future__ import annotations

import json
from pathlib import Path

import pytest

from pack_transcripts import group_into_phrases, pack_one_file
import render
from render import _words_in_range, build_master_srt
from timeline_view import find_silences


def make_transcript(path: Path) -> dict:
    return {
        "schema_version": 2,
        "source": {
            "path": str(path), "duration_s": 4.0, "size_bytes": 1,
            "mtime_ns": 1, "quick_sha256": "a", "hash_mode": "quick",
        },
        "transcription": {
            "backend": "faster-whisper", "model": "m", "device": "cuda",
            "compute_type": "float16", "language": "zh",
        },
        "words": [
            {"id": 0, "text": "大家", "start": 0.2, "end": 0.6, "kind": "word", "speaker_id": "S0", "probability": 1},
            {"id": 1, "text": "好", "start": 0.6, "end": 0.9, "kind": "word", "speaker_id": "S0", "probability": 1},
            {"id": 2, "text": "！", "start": 0.9, "end": 0.9, "kind": "punctuation", "speaker_id": None, "probability": None},
            {"id": 3, "text": "歡迎", "start": 1.8, "end": 2.3, "kind": "word", "speaker_id": "S1", "probability": 1},
        ],
        "speaker_turns": [], "events": [], "created_at": "2026-07-26T00:00:00Z",
    }


def test_pack_cjk_and_breaks() -> None:
    phrases = group_into_phrases(make_transcript(Path("a"))["words"], 0.5)
    assert [p["text"] for p in phrases] == ["大家好！", "歡迎"]


def test_pack_rejects_legacy(tmp_path: Path) -> None:
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps({"words": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        pack_one_file(path, 0.5)


def test_range_and_silence_consumers_use_v2() -> None:
    data = make_transcript(Path("a"))
    words = _words_in_range(data, 0, 1)
    assert [w["text"] for w in words] == ["大家", "好", "！"]
    assert find_silences(data["words"], 0, 3, 0.5) == [(0.9, 1.8), (2.3, 3)]


def test_build_master_srt_uses_cjk_join_and_output_offsets(tmp_path: Path) -> None:
    edit = tmp_path / "edit"
    transcripts = edit / "transcripts"
    transcripts.mkdir(parents=True)
    data = make_transcript(tmp_path / "source.mp4")
    (transcripts / "clip.json").write_text(json.dumps(data), encoding="utf-8")
    edl = {
        "sources": {"clip": str(tmp_path / "source.mp4")},
        "ranges": [
            {"source": "clip", "start": 0.0, "end": 1.0},
            {"source": "clip", "start": 1.5, "end": 2.5},
        ],
    }
    output = edit / "master.srt"
    build_master_srt(edl, edit, output)
    rendered = output.read_text(encoding="utf-8")
    assert "大家好" in rendered
    assert "00:00:01,300" in rendered


def test_windows_subtitle_filter_uses_forward_slashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path / "base.mp4"
    subs = tmp_path / "master.srt"
    base.write_bytes(b"x")
    subs.write_text("test", encoding="utf-8")
    captured: list[str] = []
    monkeypatch.setattr(render, "subtitle_force_style", lambda: "FontName=Test")
    monkeypatch.setattr(render, "run_checked", lambda command, **kwargs: captured.extend(map(str, command)))
    render.build_final_composite(base, [], subs, tmp_path / "out.mp4", tmp_path)
    filter_graph = captured[captured.index("-filter_complex") + 1]
    assert "\\tmp\\" not in filter_graph
    assert "/master.srt" in filter_graph.lower()
