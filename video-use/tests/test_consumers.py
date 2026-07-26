from __future__ import annotations

import json
import subprocess
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


def test_extract_segment_uses_aligned_audio_when_provided(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "take.mov"
    audio = tmp_path / "take.wav"
    source.write_bytes(b"video")
    audio.write_bytes(b"audio")
    captured: list[str] = []
    monkeypatch.setattr(render, "is_portrait_source", lambda path: False)
    monkeypatch.setattr(render, "is_hdr_source", lambda path: False)
    monkeypatch.setattr(render, "run_checked", lambda command, **kwargs: captured.extend(map(str, command)))
    render.extract_segment(source, 1.25, 2.0, "", tmp_path / "out.mp4", audio_source=audio)
    assert captured.count("-ss") == 2
    assert captured[captured.index("-map") + 1] == "0:v:0"
    second_map = captured.index("-map", captured.index("-map") + 1)
    assert captured[second_map + 1] == "1:a:0"
    assert str(audio) in captured


def test_extract_segment_without_manifest_keeps_single_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "take.mov"
    source.write_bytes(b"video")
    captured: list[str] = []
    monkeypatch.setattr(render, "is_portrait_source", lambda path: False)
    monkeypatch.setattr(render, "is_hdr_source", lambda path: False)
    monkeypatch.setattr(render, "run_checked", lambda command, **kwargs: captured.extend(map(str, command)))
    render.extract_segment(source, 0, 1, "", tmp_path / "out.mp4")
    assert captured.count("-i") == 1
    assert "-map" not in captured


def test_edl_loader_preserves_traditional_chinese_paths(tmp_path: Path) -> None:
    path = tmp_path / "edl.json"
    expected = r"E:\短影音\分手療癒\影片.mov"
    path.write_text(json.dumps({"sources": {"take": expected}}, ensure_ascii=False), encoding="utf-8")
    assert render.load_edl(path)["sources"]["take"] == expected


def test_portrait_probe_honors_rotation_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = json.dumps({
        "streams": [{"width": 3840, "height": 2160, "side_data_list": [{"rotation": 90}]}]
    })
    monkeypatch.setattr(
        render,
        "run_checked",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, stdout=payload, stderr=""),
    )
    assert render.is_portrait_source(tmp_path / "中文.mov") is True
