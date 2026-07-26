from __future__ import annotations

import json
import subprocess
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

import sync_media
from sync_media import (
    Alignment,
    ClipTiming,
    _alignment_from_xml,
    estimate_waveform_offset,
    find_manifest_entry,
    fps_from_rate,
    frames_to_seconds,
    parse_pluraleyes_xml,
    prepare_alignments,
)


def _xml(path: Path, video: Path, audio: Path, *, sequence: str = "Synced Sequence Replaced", duplicate_audio: bool = False) -> None:
    audio_clip = f"""
      <track><clipitem id="a1"><name>{audio.name}</name><start>272</start><end>1892</end>
      <in>272</in><out>1892</out><file id="af"><pathurl>{audio.as_uri()}</pathurl></file></clipitem></track>"""
    if duplicate_audio:
        audio_clip += f"""
      <track><clipitem id="a2"><name>{audio.name}</name><start>272</start><end>1892</end>
      <in>272</in><out>1892</out><file id="af"/></clipitem></track>"""
    path.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<xmeml><sequence><name>{sequence}</name><rate><timebase>30</timebase><ntsc>TRUE</ntsc></rate>
<media><video><track><clipitem id="v1"><name>{video.name}</name><start>272</start><end>1892</end>
<in>0</in><out>1620</out><file id="vf"><pathurl>{video.as_uri()}</pathurl></file></clipitem></track></video>
<audio>{audio_clip}</audio></media></sequence></xmeml>""",
        encoding="utf-8",
    )


def test_ntsc_rate_and_first_offset() -> None:
    fps = fps_from_rate("30", "TRUE")
    assert fps == Fraction(30_000, 1001)
    assert frames_to_seconds(272, fps) == pytest.approx(9.075733333, abs=1e-9)


def test_pluraleyes_parser_decodes_unicode_and_pairs_exactly(tmp_path: Path) -> None:
    media = tmp_path / "中文 素材"
    media.mkdir()
    video = media / "影片.mov"
    audio = media / "降噪.wav"
    video.write_bytes(b"v")
    audio.write_bytes(b"a")
    xml = tmp_path / "同步.xml"
    _xml(xml, video, audio)
    fps, pairs, available = parse_pluraleyes_xml(xml)
    assert fps == Fraction(30_000, 1001)
    assert pairs[0][0].path == video.resolve()
    assert pairs[0][1].path == audio.resolve()
    assert available == ["Synced Sequence Replaced"]


def test_pluraleyes_requires_exact_sequence_and_unambiguous_audio(tmp_path: Path) -> None:
    video, audio = tmp_path / "v.mov", tmp_path / "a.wav"
    video.write_bytes(b"v")
    audio.write_bytes(b"a")
    xml = tmp_path / "sync.xml"
    _xml(xml, video, audio, sequence="Another Sequence")
    with pytest.raises(ValueError, match="available sequences: Another Sequence"):
        parse_pluraleyes_xml(xml)
    _xml(xml, video, audio, duplicate_audio=True)
    with pytest.raises(ValueError, match="exactly one"):
        parse_pluraleyes_xml(xml)


def test_xml_alignment_uses_start_end_in_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video, audio = tmp_path / "v.mov", tmp_path / "a.wav"
    video.write_bytes(b"v")
    audio.write_bytes(b"a")
    monkeypatch.setattr(sync_media, "_probe_duration", lambda path: 100.0)
    result = _alignment_from_xml(
        ClipTiming("v", video, 272, 1892, 0, 1620),
        ClipTiming("a", audio, 272, 1892, 272, 1892),
        Fraction(30_000, 1001),
    )
    assert result.audio_offset_s == pytest.approx(9.075733333, abs=1e-9)
    assert result.coverage_video_start_s == 0
    assert result.coverage_audio_start_s == pytest.approx(9.075733333, abs=1e-9)


def test_waveform_offset_and_confidence() -> None:
    rng = np.random.default_rng(7)
    external = rng.normal(size=4000)
    reference = external[800:2800]
    offset, confidence = estimate_waveform_offset(reference, external, 400)
    assert offset == pytest.approx(2.0)
    assert confidence > 0.999


def _alignment(tmp_path: Path) -> Alignment:
    video, audio = tmp_path / "take.mov", tmp_path / "take.wav"
    video.write_bytes(b"video")
    audio.write_bytes(b"audio")
    return Alignment("take", video, audio, 5.0, 6.0, 1.0, 0.0, 5.0, 1.0, 6.0, "waveform", 0.9)


def test_manifest_is_atomic_current_and_materializes_cached_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alignment = _alignment(tmp_path)

    def fake_audio(item: Alignment, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"aligned")

    materialized: list[Path] = []

    def fake_mov(video: Path, audio: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"mov")
        materialized.append(destination)

    monkeypatch.setattr(sync_media, "build_aligned_audio", fake_audio)
    monkeypatch.setattr(sync_media, "materialize_synced_media", fake_mov)
    manifest_path = prepare_alignments([alignment], tmp_path / "edit")
    manifest = sync_media.load_sync_manifest(manifest_path)
    assert find_manifest_entry(manifest, alignment.video)["source_id"] == "take"
    prepare_alignments([alignment], tmp_path / "edit", materialize=True)
    assert materialized and materialized[0].name == "take_synced.mov"
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["sources"]["take"]["materialized_path"]
    alignment.audio.write_bytes(b"changed")
    with pytest.raises(ValueError, match="stale"):
        sync_media.load_sync_manifest(manifest_path)


def test_manifest_becomes_stale_when_xml_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alignment = _alignment(tmp_path)
    xml = tmp_path / "sync.xml"
    xml.write_text("first", encoding="utf-8")

    def fake_audio(item: Alignment, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"aligned")

    monkeypatch.setattr(sync_media, "build_aligned_audio", fake_audio)
    manifest_path = prepare_alignments(
        [alignment], tmp_path / "edit", xml_path=xml, fps=Fraction(30_000, 1001),
        sequence_name="Synced Sequence Replaced",
    )
    xml.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="PluralEyes XML changed"):
        sync_media.load_sync_manifest(manifest_path)


def test_aligned_audio_filter_pads_silence_and_replaces_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alignment = _alignment(tmp_path)
    destination = tmp_path / "edit" / "sync" / "audio" / "take.wav"
    commands: list[list[str]] = []

    def fake_run(command, *, stage, **kwargs):
        command = [str(value) for value in command]
        commands.append(command)
        if command[0] == "ffmpeg":
            Path(command[-1]).write_bytes(b"complete")
        return subprocess.CompletedProcess(command, 0, stdout="5.0\n", stderr="")

    monkeypatch.setattr(sync_media, "run_checked", fake_run)
    sync_media.build_aligned_audio(alignment, destination)
    assert destination.read_bytes() == b"complete"
    graph = commands[0][commands[0].index("-filter_complex") + 1]
    assert "adelay=0S" in graph
    assert "apad" in graph
    assert "atrim=duration=5.000000000" in graph


def test_waveform_failure_report_is_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sync_media,
        "alignment_from_waveforms",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("confidence too low")),
    )
    with pytest.raises(ValueError, match="confidence"):
        sync_media.prepare_waveform_sync(tmp_path / "v.mov", tmp_path / "a.wav", tmp_path / "edit")
    report = json.loads((tmp_path / "edit" / "sync" / "waveform_failure.json").read_text())
    assert report["minimum_confidence"] == sync_media.WAVEFORM_MIN_CONFIDENCE
