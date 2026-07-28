from __future__ import annotations

import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

import pytest

import edl
import export_premiere_xml as pxml
import render
from sync_media import _clean_path, fps_from_rate, rate_from_fps


# -------- ffprobe stubs ------------------------------------------------------


DEFAULT_SPEC = {
    "width": 3840,
    "height": 2160,
    "r_frame_rate": "30/1",
    "avg_frame_rate": "30/1",
    "nb_frames": "9000",
    "duration": "300.0",
    "channels": 2,
    "pix_fmt": "yuv420p",
    "rotation": None,
    "video": True,
    "audio": True,
}


def spec(**overrides) -> dict:
    merged = dict(DEFAULT_SPEC)
    merged.update(overrides)
    return merged


def _payload(item: dict) -> dict:
    streams = []
    if item["video"]:
        stream = {
            "index": 0,
            "codec_type": "video",
            "r_frame_rate": item["r_frame_rate"],
            "avg_frame_rate": item["avg_frame_rate"],
            "width": item["width"],
            "height": item["height"],
            "pix_fmt": item["pix_fmt"],
        }
        if item["nb_frames"] is not None:
            stream["nb_frames"] = item["nb_frames"]
        if item["rotation"] is not None:
            stream["side_data_list"] = [{"rotation": item["rotation"]}]
        streams.append(stream)
    if item["audio"]:
        streams.append({"index": 1, "codec_type": "audio", "channels": item["channels"]})
    return {"streams": streams, "format": {"duration": item["duration"]}}


def install_probe(monkeypatch: pytest.MonkeyPatch, specs: dict[str, dict]) -> None:
    """Answer every ffprobe call from `specs`, keyed by file name."""

    def fake_run(command, *, stage: str, **kwargs):
        name = Path(command[-1]).name
        if name not in specs:
            raise AssertionError(f"unexpected ffprobe call for {name}")
        return subprocess.CompletedProcess(
            command, 0, stdout=json.dumps(_payload(specs[name])), stderr=""
        )

    monkeypatch.setattr(edl, "run_checked", fake_run)


@pytest.fixture(autouse=True)
def clear_stream_cache():
    pxml._STREAM_CACHE.clear()
    yield
    pxml._STREAM_CACHE.clear()


# -------- EDL builders -------------------------------------------------------


def write_edl(tmp_path: Path, edl_body: dict, source_names: list[str]) -> Path:
    edit_dir = tmp_path / "edit"
    edit_dir.mkdir(parents=True, exist_ok=True)
    for name in source_names:
        (tmp_path / name).write_bytes(b"")
    edl_path = edit_dir / "edl.json"
    edl_path.write_text(json.dumps(edl_body), encoding="utf-8")
    return edl_path


def simple_edl(tmp_path: Path, ranges: list[dict] | None = None, **extra) -> Path:
    if ranges is None:
        ranges = [
            {"source": "A", "start": 2.0, "end": 5.0, "beat": "HOOK", "reason": "cleanest take"},
            {"source": "A", "start": 10.0, "end": 12.5, "beat": "TURN", "reason": "no false start"},
        ]
    body = {
        "version": 1,
        "sources": {"A": str(tmp_path / "A.MP4")},
        "ranges": ranges,
        **extra,
    }
    return write_edl(tmp_path, body, ["A.MP4"])


def parse(out_path: Path) -> ET.Element:
    return ET.parse(out_path).getroot()


def video_tracks(root: ET.Element) -> list[ET.Element]:
    return root.findall("./sequence/media/video/track")


def audio_tracks(root: ET.Element) -> list[ET.Element]:
    return root.findall("./sequence/media/audio/track")


# -------- Frame rate ---------------------------------------------------------


@pytest.mark.parametrize(
    "fps",
    [
        Fraction(24), Fraction(25), Fraction(30), Fraction(50), Fraction(60),
        Fraction(24000, 1001), Fraction(30000, 1001), Fraction(60000, 1001),
    ],
)
def test_rate_roundtrip_is_exact_for_canonical_rates(fps: Fraction) -> None:
    timebase, ntsc = rate_from_fps(fps)
    assert fps_from_rate(timebase, ntsc) == fps


def test_rate_refuses_decimal_approximations() -> None:
    # 23.976 as a decimal is not 24000/1001. Rounding it silently would move
    # every cut, so it has to be rejected rather than accepted approximately.
    with pytest.raises(ValueError, match="neither an integer nor an NTSC rate"):
        rate_from_fps(Fraction("23.976"))


def test_cli_fps_snaps_ntsc_decimals() -> None:
    assert pxml.coerce_fps("23.976") == Fraction(24000, 1001)
    assert pxml.coerce_fps("29.97") == Fraction(30000, 1001)
    assert pxml.coerce_fps(30) == Fraction(30)


def test_mixed_frame_rates_are_rejected(tmp_path: Path, monkeypatch) -> None:
    body = {
        "version": 1,
        "sources": {"A": str(tmp_path / "A.MP4"), "B": str(tmp_path / "B.MP4")},
        "ranges": [
            {"source": "A", "start": 0.0, "end": 2.0},
            {"source": "B", "start": 0.0, "end": 2.0},
        ],
    }
    edl_path = write_edl(tmp_path, body, ["A.MP4", "B.MP4"])
    install_probe(monkeypatch, {
        "A.MP4": spec(),
        "B.MP4": spec(r_frame_rate="30000/1001", avg_frame_rate="30000/1001"),
    })
    with pytest.raises(ValueError, match="do not share one frame rate"):
        pxml.export(edl_path, tmp_path / "out.xml")


def test_variable_frame_rate_is_rejected(tmp_path: Path, monkeypatch) -> None:
    edl_path = simple_edl(tmp_path)
    install_probe(monkeypatch, {"A.MP4": spec(avg_frame_rate="28.2")})
    with pytest.raises(ValueError, match="variable-frame-rate"):
        pxml.export(edl_path, tmp_path / "out.xml")


def test_fps_override_bypasses_the_refusals(tmp_path: Path, monkeypatch, capsys) -> None:
    edl_path = simple_edl(tmp_path)
    install_probe(monkeypatch, {"A.MP4": spec(avg_frame_rate="28.2")})
    pxml.export(edl_path, tmp_path / "out.xml", fps_override=Fraction(30))
    assert "overrides variable-frame-rate" in capsys.readouterr().out


def test_render_fps_mismatch_warns(tmp_path: Path, monkeypatch, capsys) -> None:
    edl_path = simple_edl(tmp_path, fps=24)
    install_probe(monkeypatch, {"A.MP4": spec()})
    pxml.export(edl_path, tmp_path / "out.xml")
    assert "will not match the rendered video" in capsys.readouterr().out


# -------- Timeline structure -------------------------------------------------


def test_timeline_has_no_gaps_or_overlaps(tmp_path: Path, monkeypatch) -> None:
    ranges = [
        {"source": "A", "start": 2.0, "end": 5.0},
        {"source": "A", "start": 10.0, "end": 12.5},
        {"source": "A", "start": 20.0, "end": 21.1},
    ]
    edl_path = simple_edl(tmp_path, ranges=ranges)
    install_probe(monkeypatch, {"A.MP4": spec()})
    out = pxml.export(edl_path, tmp_path / "out.xml")

    clips = video_tracks(parse(out))[0].findall("clipitem")
    assert len(clips) == 3
    assert clips[0].findtext("start") == "0"
    for previous, current in zip(clips, clips[1:]):
        assert current.findtext("start") == previous.findtext("end")


def test_source_in_out_length_matches_timeline_length(tmp_path: Path, monkeypatch) -> None:
    edl_path = simple_edl(tmp_path)
    install_probe(monkeypatch, {"A.MP4": spec()})
    out = pxml.export(edl_path, tmp_path / "out.xml")

    for clip in video_tracks(parse(out))[0].findall("clipitem"):
        source_length = int(clip.findtext("out")) - int(clip.findtext("in"))
        timeline_length = int(clip.findtext("end")) - int(clip.findtext("start"))
        assert source_length == timeline_length


def test_each_physical_file_is_defined_once(tmp_path: Path, monkeypatch) -> None:
    edl_path = simple_edl(tmp_path)
    install_probe(monkeypatch, {"A.MP4": spec()})
    out = pxml.export(edl_path, tmp_path / "out.xml")

    files = parse(out).iter("file")
    defined = [node for node in files if list(node)]
    # Two ranges plus two audio channels each reference the same camera file;
    # exactly one of those references may carry the definition.
    assert len(defined) == 1
    assert defined[0].findtext("name") == "A.MP4"


def test_file_carries_full_source_duration(tmp_path: Path, monkeypatch) -> None:
    edl_path = simple_edl(tmp_path)
    install_probe(monkeypatch, {"A.MP4": spec(nb_frames="9000")})
    out = pxml.export(edl_path, tmp_path / "out.xml")

    defined = next(node for node in parse(out).iter("file") if list(node))
    # The whole 9000-frame source, not the 90 frames the first cut uses —
    # this is what lets Premiere drag a cut open.
    assert defined.findtext("duration") == "9000"


def test_estimated_duration_warns_when_frame_count_missing(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    edl_path = simple_edl(tmp_path)
    install_probe(monkeypatch, {"A.MP4": spec(nb_frames=None)})
    pxml.export(edl_path, tmp_path / "out.xml")
    assert "its length is estimated" in capsys.readouterr().out


def test_sequence_format_is_written(tmp_path: Path, monkeypatch) -> None:
    edl_path = simple_edl(tmp_path)
    install_probe(monkeypatch, {"A.MP4": spec()})
    out = pxml.export(edl_path, tmp_path / "out.xml")

    characteristics = parse(out).find("./sequence/media/video/format/samplecharacteristics")
    assert characteristics.findtext("width") == "1920"
    assert characteristics.findtext("height") == "1080"
    assert characteristics.findtext("pixelaspectratio") == "square"


def test_portrait_source_gets_a_vertical_sequence(tmp_path: Path, monkeypatch) -> None:
    edl_path = simple_edl(tmp_path)
    install_probe(monkeypatch, {"A.MP4": spec(width=2160, height=3840)})
    out = pxml.export(edl_path, tmp_path / "out.xml")

    characteristics = parse(out).find("./sequence/media/video/format/samplecharacteristics")
    assert (characteristics.findtext("width"), characteristics.findtext("height")) == ("1080", "1920")


def test_rotation_metadata_decides_orientation(tmp_path: Path, monkeypatch) -> None:
    edl_path = simple_edl(tmp_path)
    install_probe(monkeypatch, {"A.MP4": spec(width=3840, height=2160, rotation=-90)})
    out = pxml.export(edl_path, tmp_path / "out.xml")

    characteristics = parse(out).find("./sequence/media/video/format/samplecharacteristics")
    assert (characteristics.findtext("width"), characteristics.findtext("height")) == ("1080", "1920")


# -------- Links, scale, markers ----------------------------------------------


def test_video_and_audio_clips_are_linked(tmp_path: Path, monkeypatch) -> None:
    edl_path = simple_edl(tmp_path)
    install_probe(monkeypatch, {"A.MP4": spec(channels=2)})
    out = pxml.export(edl_path, tmp_path / "out.xml")
    root = parse(out)

    video_clip = video_tracks(root)[0].find("clipitem")
    refs = [link.findtext("linkclipref") for link in video_clip.findall("link")]
    assert refs == ["clipitem-v-0", "clipitem-a1-0", "clipitem-a2-0"]

    # Every member of the group carries the same link block, which is what
    # makes Premiere move picture and sound together.
    for track_index, track in enumerate(audio_tracks(root), start=1):
        audio_clip = track.find("clipitem")
        assert [link.findtext("linkclipref") for link in audio_clip.findall("link")] == refs
        assert audio_clip.find("sourcetrack/mediatype").text == "audio"
        assert audio_clip.find("sourcetrack/trackindex").text == str(track_index)


def test_punch_scale_is_relative_to_the_fitted_size(tmp_path: Path, monkeypatch) -> None:
    ranges = [
        {"source": "A", "start": 0.0, "end": 2.0},
        {"source": "A", "start": 5.0, "end": 7.0, "punch": 1.15},
    ]
    edl_path = simple_edl(tmp_path, ranges=ranges)
    install_probe(monkeypatch, {"A.MP4": spec(width=3840, height=2160)})
    out = pxml.export(edl_path, tmp_path / "out.xml")

    clips = video_tracks(parse(out))[0].findall("clipitem")
    # 4K into a 1080 sequence fits at 50%. Writing punch * 100 would put this
    # clip at 115% of native 4K — more than four times too big.
    assert float(clips[0].find(".//parameter/value").text) == pytest.approx(50.0)
    assert float(clips[1].find(".//parameter/value").text) == pytest.approx(57.5)


def test_matching_resolution_needs_no_scale_filter(tmp_path: Path, monkeypatch) -> None:
    ranges = [
        {"source": "A", "start": 0.0, "end": 2.0},
        {"source": "A", "start": 5.0, "end": 7.0, "punch": 1.15},
    ]
    edl_path = simple_edl(tmp_path, ranges=ranges)
    install_probe(monkeypatch, {"A.MP4": spec(width=1920, height=1080)})
    out = pxml.export(edl_path, tmp_path / "out.xml")

    clips = video_tracks(parse(out))[0].findall("clipitem")
    assert clips[0].find("filter") is None
    assert float(clips[1].find(".//parameter/value").text) == pytest.approx(115.0)


def test_no_punch_removes_every_scale_filter(tmp_path: Path, monkeypatch) -> None:
    ranges = [{"source": "A", "start": 5.0, "end": 7.0, "punch": 1.15}]
    edl_path = simple_edl(tmp_path, ranges=ranges)
    install_probe(monkeypatch, {"A.MP4": spec()})
    out = pxml.export(edl_path, tmp_path / "out.xml", with_punch=False)

    assert parse(out).find(".//filter") is None


def test_markers_carry_beat_and_reason(tmp_path: Path, monkeypatch) -> None:
    edl_path = simple_edl(tmp_path)
    install_probe(monkeypatch, {"A.MP4": spec()})
    out = pxml.export(edl_path, tmp_path / "out.xml")

    markers = parse(out).findall("./sequence/marker")
    assert [m.findtext("name") for m in markers] == ["HOOK", "TURN"]
    assert markers[0].findtext("comment") == "cleanest take"
    assert markers[0].findtext("in") == "0"


def test_markers_can_be_switched_off(tmp_path: Path, monkeypatch) -> None:
    edl_path = simple_edl(tmp_path)
    install_probe(monkeypatch, {"A.MP4": spec()})
    out = pxml.export(edl_path, tmp_path / "out.xml", with_markers=False)
    assert parse(out).findall("./sequence/marker") == []


def test_grade_is_recorded_in_the_clip_name(tmp_path: Path, monkeypatch) -> None:
    edl_path = simple_edl(tmp_path, grade="warm_cinematic")
    install_probe(monkeypatch, {"A.MP4": spec()})
    out = pxml.export(edl_path, tmp_path / "out.xml")

    name = video_tracks(parse(out))[0].find("clipitem").findtext("name")
    assert "warm_cinematic" in name


# -------- Overlays -----------------------------------------------------------


def test_overlay_lands_on_a_second_video_track(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "slot.mov").write_bytes(b"")
    edl_path = simple_edl(
        tmp_path,
        overlays=[{"file": str(tmp_path / "slot.mov"), "start_in_output": 1.0, "duration": 2.0}],
    )
    install_probe(monkeypatch, {
        "A.MP4": spec(),
        "slot.mov": spec(width=1920, height=1080, duration="2.0", pix_fmt="yuva444p10le", audio=False),
    })
    out = pxml.export(edl_path, tmp_path / "out.xml")

    tracks = video_tracks(parse(out))
    assert len(tracks) == 2
    overlay = tracks[1].find("clipitem")
    assert overlay.findtext("start") == "30"
    assert overlay.findtext("end") == "90"


def test_overlay_shorter_than_declared_is_rejected(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "slot.mov").write_bytes(b"")
    edl_path = simple_edl(
        tmp_path,
        overlays=[{"file": str(tmp_path / "slot.mov"), "start_in_output": 0.0, "duration": 5.0}],
    )
    install_probe(monkeypatch, {
        "A.MP4": spec(),
        "slot.mov": spec(duration="2.0", audio=False),
    })
    with pytest.raises(ValueError, match="EDL places"):
        pxml.export(edl_path, tmp_path / "out.xml")


def test_opaque_overlay_warns(tmp_path: Path, monkeypatch, capsys) -> None:
    (tmp_path / "slot.mov").write_bytes(b"")
    edl_path = simple_edl(
        tmp_path,
        overlays=[{"file": str(tmp_path / "slot.mov"), "start_in_output": 0.0, "duration": 2.0}],
    )
    install_probe(monkeypatch, {
        "A.MP4": spec(),
        "slot.mov": spec(duration="2.0", pix_fmt="yuv420p", audio=False),
    })
    pxml.export(edl_path, tmp_path / "out.xml")
    assert "no alpha channel" in capsys.readouterr().out


# -------- Paths and external audio -------------------------------------------


def test_pathurl_roundtrips_through_the_projects_own_parser(tmp_path: Path, monkeypatch) -> None:
    folder = tmp_path / "課程錄影"
    folder.mkdir()
    source = folder / "第一講.MP4"
    source.write_bytes(b"")
    body = {
        "version": 1,
        "sources": {"A": str(source)},
        "ranges": [{"source": "A", "start": 0.0, "end": 2.0}],
    }
    edit_dir = tmp_path / "edit"
    edit_dir.mkdir()
    edl_path = edit_dir / "edl.json"
    edl_path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")

    install_probe(monkeypatch, {"第一講.MP4": spec()})
    out = pxml.export(edl_path, tmp_path / "out.xml")

    defined = next(node for node in parse(out).iter("file") if list(node))
    # sync_media already reads FCP7 XML from PluralEyes; making it read ours
    # back proves the encoding survives, though not that Premiere accepts it.
    assert _clean_path(defined.findtext("pathurl")) == source.resolve()


def test_external_audio_points_at_the_aligned_wav(tmp_path: Path, monkeypatch) -> None:
    wav = tmp_path / "A_aligned.wav"
    wav.write_bytes(b"")
    manifest = {"sources": {"A": {"alignment": {"aligned_audio": {"path": str(wav)}}}}}
    (tmp_path / "edit").mkdir(parents=True, exist_ok=True)
    (tmp_path / "edit" / "sync_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    edl_path = simple_edl(tmp_path, sync_manifest="sync_manifest.json")
    install_probe(monkeypatch, {"A.MP4": spec(), "A_aligned.wav": spec(video=False, channels=2)})
    monkeypatch.setattr(pxml, "load_sync_manifest", lambda path: manifest)
    monkeypatch.setattr(pxml, "find_manifest_entry", lambda m, video: m["sources"]["A"])

    out = pxml.export(edl_path, tmp_path / "out.xml")
    root = parse(out)

    audio_clip = audio_tracks(root)[0].find("clipitem")
    assert audio_clip.findtext("name") == "A_aligned.wav"
    video_clip = video_tracks(root)[0].find("clipitem")
    assert video_clip.findtext("name").endswith("A.MP4")


def test_many_channel_source_warns_about_dropped_channels(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    edl_path = simple_edl(tmp_path)
    install_probe(monkeypatch, {"A.MP4": spec(channels=6)})
    pxml.export(edl_path, tmp_path / "out.xml")
    assert "drops the rest" in capsys.readouterr().out


def test_silent_source_produces_no_audio_track(tmp_path: Path, monkeypatch) -> None:
    edl_path = simple_edl(tmp_path)
    install_probe(monkeypatch, {"A.MP4": spec(audio=False, channels=0)})
    out = pxml.export(edl_path, tmp_path / "out.xml")
    assert audio_tracks(parse(out)) == []


# -------- EDL validation -----------------------------------------------------


def test_missing_source_file_is_rejected(tmp_path: Path) -> None:
    edit_dir = tmp_path / "edit"
    edit_dir.mkdir()
    body = {
        "version": 1,
        "sources": {"A": str(tmp_path / "gone.MP4")},
        "ranges": [{"source": "A", "start": 0.0, "end": 1.0}],
    }
    edl_path = edit_dir / "edl.json"
    edl_path.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(ValueError, match="do not exist"):
        pxml.export(edl_path, tmp_path / "out.xml")


@pytest.mark.parametrize(
    "bad_range, message",
    [
        ({"source": "Z", "start": 0.0, "end": 1.0}, "unknown source"),
        ({"source": "A", "start": 5.0, "end": 5.0}, "ends at or before"),
        ({"source": "A", "start": -1.0, "end": 2.0}, "starts before zero"),
        ({"source": "A", "start": 0.0, "end": float("inf")}, "non-finite"),
        ({"source": "A", "start": 0.0, "end": 1.0, "punch": 0.5}, "below 1.0"),
    ],
)
def test_invalid_ranges_are_rejected(tmp_path: Path, bad_range: dict, message: str) -> None:
    edl_path = simple_edl(tmp_path, ranges=[bad_range])
    with pytest.raises(ValueError, match=message):
        pxml.export(edl_path, tmp_path / "out.xml")


def test_empty_ranges_are_rejected(tmp_path: Path) -> None:
    edl_path = simple_edl(tmp_path, ranges=[])
    with pytest.raises(ValueError, match="at least one range"):
        pxml.export(edl_path, tmp_path / "out.xml")


def test_cut_past_the_end_of_the_media_is_rejected(tmp_path: Path, monkeypatch) -> None:
    edl_path = simple_edl(tmp_path, ranges=[{"source": "A", "start": 0.0, "end": 400.0}])
    install_probe(monkeypatch, {"A.MP4": spec(duration="300.0")})
    with pytest.raises(ValueError, match="only 300"):
        pxml.export(edl_path, tmp_path / "out.xml")


# -------- Atomic write and failure behaviour ---------------------------------


def test_failed_export_leaves_no_partial_file_and_clears_the_stale_one(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    out_path = tmp_path / "final.xml"
    out_path.write_text("<xmeml>previous render</xmeml>", encoding="utf-8")
    (tmp_path / "final.srt").write_text("1\n", encoding="utf-8")

    edl_path = simple_edl(tmp_path)
    install_probe(monkeypatch, {"A.MP4": spec(avg_frame_rate="20.0")})

    assert pxml.export_or_clear(edl_path, out_path) is None
    # A stale XML beside a fresh video reads as its companion, so it goes.
    assert not out_path.exists()
    assert not (tmp_path / "final.srt").exists()
    assert not (tmp_path / "final.xml.tmp").exists()
    assert "no Premiere XML written" in capsys.readouterr().out


def test_successful_export_leaves_no_temp_file(tmp_path: Path, monkeypatch) -> None:
    edl_path = simple_edl(tmp_path)
    install_probe(monkeypatch, {"A.MP4": spec()})
    out = pxml.export(edl_path, tmp_path / "final.xml")
    assert out.exists()
    assert not (tmp_path / "final.xml.tmp").exists()


# -------- Subtitles ----------------------------------------------------------


def test_edl_subtitle_path_wins_over_master_srt(tmp_path: Path, monkeypatch) -> None:
    edit_dir = tmp_path / "edit"
    edit_dir.mkdir(parents=True, exist_ok=True)
    (edit_dir / "master.srt").write_text("stale\n", encoding="utf-8")
    (edit_dir / "custom.srt").write_text("chosen\n", encoding="utf-8")

    edl_path = simple_edl(tmp_path, subtitles="custom.srt")
    install_probe(monkeypatch, {"A.MP4": spec()})
    pxml.export(edl_path, tmp_path / "final.xml")

    assert (tmp_path / "final.srt").read_text(encoding="utf-8") == "chosen\n"


def test_master_srt_is_the_fallback(tmp_path: Path, monkeypatch) -> None:
    edit_dir = tmp_path / "edit"
    edit_dir.mkdir(parents=True, exist_ok=True)
    (edit_dir / "master.srt").write_text("fallback\n", encoding="utf-8")

    edl_path = simple_edl(tmp_path)
    install_probe(monkeypatch, {"A.MP4": spec()})
    pxml.export(edl_path, tmp_path / "final.xml")

    assert (tmp_path / "final.srt").read_text(encoding="utf-8") == "fallback\n"


def test_no_subtitles_copies_nothing(tmp_path: Path, monkeypatch) -> None:
    edit_dir = tmp_path / "edit"
    edit_dir.mkdir(parents=True, exist_ok=True)
    (edit_dir / "master.srt").write_text("fallback\n", encoding="utf-8")

    edl_path = simple_edl(tmp_path)
    install_probe(monkeypatch, {"A.MP4": spec()})
    pxml.export(edl_path, tmp_path / "final.xml", with_subtitles=False)

    assert not (tmp_path / "final.srt").exists()


# -------- Real ffprobe payloads ----------------------------------------------
#
# The stubs above shape their own JSON, so these two keep a copy of what
# ffprobe actually emits — including the N/A fields and the phone-footage
# rate drift that the stubs would otherwise never exercise.


REAL_SONY_PROBE = """
{"streams":[{"index":0,"codec_type":"video","width":3840,"height":2160,
"pix_fmt":"yuv420p10le","r_frame_rate":"30000/1001","avg_frame_rate":"30000/1001",
"nb_frames":"4288"},{"index":1,"codec_type":"audio","channels":2}],
"format":{"duration":"143.076733"}}
"""

REAL_IPHONE_VFR_PROBE = """
{"streams":[{"index":0,"codec_type":"video","width":1920,"height":1080,
"pix_fmt":"yuv420p","r_frame_rate":"30/1","avg_frame_rate":"2334881/81920",
"nb_frames":"N/A","side_data_list":[{"side_data_type":"Display Matrix","rotation":-90}]},
{"index":1,"codec_type":"audio","channels":1}],"format":{"duration":"81.920000"}}
"""


def _install_raw_probe(monkeypatch, payload: str) -> None:
    monkeypatch.setattr(
        edl, "run_checked",
        lambda command, *, stage, **kwargs: subprocess.CompletedProcess(command, 0, stdout=payload, stderr=""),
    )


def test_parses_real_constant_rate_probe(tmp_path: Path, monkeypatch) -> None:
    _install_raw_probe(monkeypatch, REAL_SONY_PROBE)
    source = tmp_path / "C0103.MP4"
    source.write_bytes(b"")

    rate = edl.probe_rate(source)
    assert rate.nominal == Fraction(30000, 1001)
    assert rate.is_variable is False
    assert edl.duration_in_frames(rate) == (4288, True)

    streams = edl.probe_streams(source)
    assert (streams.display_width, streams.display_height) == (3840, 2160)
    assert streams.has_alpha is False


def test_parses_real_variable_rate_probe(tmp_path: Path, monkeypatch) -> None:
    _install_raw_probe(monkeypatch, REAL_IPHONE_VFR_PROBE)
    source = tmp_path / "IMG_0001.MOV"
    source.write_bytes(b"")

    rate = edl.probe_rate(source)
    assert rate.nominal == Fraction(30)
    assert rate.is_variable is True
    # nb_frames is N/A here, so the length has to be estimated.
    assert edl.duration_in_frames(rate) == (2458, False)

    streams = edl.probe_streams(source)
    # Rotation metadata makes this a vertical clip despite 1920x1080 storage.
    assert (streams.display_width, streams.display_height) == (1080, 1920)
    assert streams.audio_channels == 1


# -------- render.py integration ----------------------------------------------


def test_render_writes_the_xml_beside_the_video(tmp_path: Path, monkeypatch) -> None:
    edl_path = simple_edl(tmp_path)
    install_probe(monkeypatch, {"A.MP4": spec()})
    out_video = tmp_path / "final.mp4"

    monkeypatch.setattr(render, "extract_all_segments", lambda *a, **k: [])
    monkeypatch.setattr(
        render, "concat_segments",
        lambda segments, out, edit_dir: out.write_bytes(b"base"),
    )
    monkeypatch.setattr(
        render, "build_final_composite",
        lambda base, overlays, subs, out, edit_dir: out.write_bytes(b"video"),
    )
    monkeypatch.setattr(sys, "argv", [
        "render.py", str(edl_path), "-o", str(out_video), "--no-loudnorm",
    ])

    render.main()

    assert out_video.exists()
    assert (tmp_path / "final.xml").exists()
    assert len(video_tracks(parse(tmp_path / "final.xml"))[0].findall("clipitem")) == 2


def test_render_no_xml_flag_skips_the_export(tmp_path: Path, monkeypatch) -> None:
    edl_path = simple_edl(tmp_path)
    install_probe(monkeypatch, {"A.MP4": spec()})
    out_video = tmp_path / "final.mp4"

    monkeypatch.setattr(render, "extract_all_segments", lambda *a, **k: [])
    monkeypatch.setattr(
        render, "concat_segments",
        lambda segments, out, edit_dir: out.write_bytes(b"base"),
    )
    monkeypatch.setattr(
        render, "build_final_composite",
        lambda base, overlays, subs, out, edit_dir: out.write_bytes(b"video"),
    )
    monkeypatch.setattr(sys, "argv", [
        "render.py", str(edl_path), "-o", str(out_video), "--no-loudnorm", "--no-xml",
    ])

    render.main()

    assert out_video.exists()
    assert not (tmp_path / "final.xml").exists()


def test_render_survives_a_failing_export(tmp_path: Path, monkeypatch, capsys) -> None:
    edl_path = simple_edl(tmp_path)
    install_probe(monkeypatch, {"A.MP4": spec(avg_frame_rate="20.0")})
    out_video = tmp_path / "final.mp4"
    (tmp_path / "final.xml").write_text("<xmeml>stale</xmeml>", encoding="utf-8")

    monkeypatch.setattr(render, "extract_all_segments", lambda *a, **k: [])
    monkeypatch.setattr(
        render, "concat_segments",
        lambda segments, out, edit_dir: out.write_bytes(b"base"),
    )
    monkeypatch.setattr(
        render, "build_final_composite",
        lambda base, overlays, subs, out, edit_dir: out.write_bytes(b"video"),
    )
    monkeypatch.setattr(sys, "argv", [
        "render.py", str(edl_path), "-o", str(out_video), "--no-loudnorm",
    ])

    render.main()

    # The render is the expensive artifact; a broken export must not cost it.
    assert out_video.exists()
    assert not (tmp_path / "final.xml").exists()
    assert "no Premiere XML written" in capsys.readouterr().out
