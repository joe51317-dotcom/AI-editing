"""Align external audio to source videos using PluralEyes XML or waveforms.

Original media is never modified. The canonical outputs are an atomic
``sync_manifest.json`` plus one video-length PCM WAV per source under
``<edit>/sync/audio``. Optional materialized MOV files stream-copy video and
mux the aligned PCM audio for Premiere hand-off.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
import urllib.parse
import wave
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np

from config import configure_console
from subprocess_utils import run_checked
from transcript_v2 import file_sha256, utc_now


MANIFEST_VERSION = 1
DEFAULT_SEQUENCE = "Synced Sequence Replaced"
DEFAULT_SAMPLE_RATE = 48_000
WAVEFORM_ANALYSIS_RATE = 4_000
WAVEFORM_MIN_CONFIDENCE = 0.25


@dataclass(frozen=True)
class ClipTiming:
    name: str
    path: Path
    timeline_start: int
    timeline_end: int
    source_in: int
    source_out: int


@dataclass(frozen=True)
class Alignment:
    source_id: str
    video: Path
    audio: Path
    video_duration_s: float
    audio_duration_s: float
    audio_offset_s: float
    coverage_video_start_s: float
    coverage_video_end_s: float
    coverage_audio_start_s: float
    coverage_audio_end_s: float
    backend: str
    confidence: float | None = None


def _media_signature(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    stat = resolved.stat()
    return {
        "path": str(resolved),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "quick_sha256": file_sha256(resolved),
    }


def _probe_duration(path: Path) -> float:
    result = run_checked(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", path,
        ],
        stage=f"probe duration for {path.name}",
    )
    try:
        duration = float(result.stdout.strip())
    except ValueError as exc:
        raise RuntimeError(f"ffprobe returned an invalid duration for {path}") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise RuntimeError(f"media duration must be positive: {path}")
    return duration


def _clean_path(path_url: str | None) -> Path:
    if not path_url:
        raise ValueError("XML media file is missing pathurl")
    decoded = urllib.parse.unquote(path_url.strip())
    if decoded.lower().startswith("file://localhost/"):
        decoded = decoded[len("file://localhost/"):]
    elif decoded.lower().startswith("file:///"):
        decoded = decoded[len("file:///"):]
    decoded = decoded.replace("/", os.sep)
    if os.name == "nt" and len(decoded) >= 3 and decoded[0] == os.sep and decoded[2] == ":":
        decoded = decoded[1:]
    return Path(decoded).resolve()


def fps_from_rate(timebase: str | int, ntsc: str | bool) -> Fraction:
    base = int(timebase)
    if base <= 0:
        raise ValueError(f"invalid XML timebase: {timebase}")
    is_ntsc = str(ntsc).strip().upper() in {"TRUE", "1", "YES"}
    return Fraction(base * 1000, 1001) if is_ntsc else Fraction(base, 1)


def frames_to_seconds(frames: int | float, fps: Fraction) -> float:
    return float(Fraction(frames) / fps)


def _int_text(node: ET.Element, name: str) -> int:
    value = node.findtext(name)
    if value is None:
        raise ValueError(f"XML clip {node.findtext('name')!r} is missing {name}")
    return int(value)


def _file_path(node: ET.Element, files_by_id: dict[str, Path]) -> Path:
    file_node = node.find("file")
    if file_node is None:
        raise ValueError(f"XML clip {node.findtext('name')!r} is missing file")
    path_url = file_node.findtext("pathurl")
    if path_url:
        return _clean_path(path_url)
    file_id = file_node.get("id")
    if file_id and file_id in files_by_id:
        return files_by_id[file_id]
    raise ValueError(f"XML clip {node.findtext('name')!r} has an unresolved file reference")


def _parse_clip(node: ET.Element, files_by_id: dict[str, Path]) -> ClipTiming:
    return ClipTiming(
        name=(node.findtext("name") or "").strip(),
        path=_file_path(node, files_by_id),
        timeline_start=_int_text(node, "start"),
        timeline_end=_int_text(node, "end"),
        source_in=_int_text(node, "in"),
        source_out=_int_text(node, "out"),
    )


def parse_pluraleyes_xml(
    xml_path: Path,
    *,
    sequence_name: str = DEFAULT_SEQUENCE,
) -> tuple[Fraction, list[tuple[ClipTiming, ClipTiming]], list[str]]:
    """Return exact video/audio pairs from one named PluralEyes sequence."""
    try:
        root = ET.parse(xml_path).getroot()
    except (ET.ParseError, OSError) as exc:
        raise ValueError(f"could not parse PluralEyes XML {xml_path}: {exc}") from exc

    files_by_id: dict[str, Path] = {}
    for file_node in root.findall(".//file"):
        file_id = file_node.get("id")
        path_url = file_node.findtext("pathurl")
        if file_id and path_url:
            files_by_id[file_id] = _clean_path(path_url)

    sequences = root.findall(".//sequence")
    available = [seq.findtext("name") or "<unnamed>" for seq in sequences]
    sequence = next((seq for seq in sequences if seq.findtext("name") == sequence_name), None)
    if sequence is None:
        raise ValueError(
            f"sequence {sequence_name!r} was not found; available sequences: {', '.join(available)}"
        )
    rate = sequence.find("rate")
    if rate is None:
        raise ValueError(f"sequence {sequence_name!r} is missing rate")
    fps = fps_from_rate(rate.findtext("timebase") or "", rate.findtext("ntsc") or "FALSE")

    videos = [
        _parse_clip(node, files_by_id)
        for node in sequence.findall("./media/video/track/clipitem")
    ]
    audio_candidates = [
        _parse_clip(node, files_by_id)
        for node in sequence.findall("./media/audio/track/clipitem")
        if Path(node.findtext("name") or "").suffix.lower() in {".wav", ".flac", ".aif", ".aiff"}
    ]
    if not videos:
        raise ValueError(f"sequence {sequence_name!r} contains no video clips")

    pairs: list[tuple[ClipTiming, ClipTiming]] = []
    for video in videos:
        overlaps = [
            audio for audio in audio_candidates
            if audio.timeline_start < video.timeline_end and audio.timeline_end > video.timeline_start
        ]
        if len(overlaps) != 1:
            names = ", ".join(audio.name for audio in overlaps) or "none"
            raise ValueError(
                f"video {video.path} must overlap exactly one external audio clip; found: {names}"
            )
        pairs.append((video, overlaps[0]))
    return fps, pairs, available


def _alignment_from_xml(video: ClipTiming, audio: ClipTiming, fps: Fraction) -> Alignment:
    if not video.path.is_file():
        raise FileNotFoundError(f"video referenced by XML does not exist: {video.path}")
    if not audio.path.is_file():
        raise FileNotFoundError(f"audio referenced by XML does not exist: {audio.path}")
    video_duration = _probe_duration(video.path)
    audio_duration = _probe_duration(audio.path)
    overlap_start = max(video.timeline_start, audio.timeline_start)
    overlap_end = min(video.timeline_end, audio.timeline_end)
    if overlap_end <= overlap_start:
        raise ValueError(f"XML clips do not overlap: {video.name} / {audio.name}")

    video_start = frames_to_seconds(video.source_in + overlap_start - video.timeline_start, fps)
    video_end = frames_to_seconds(video.source_in + overlap_end - video.timeline_start, fps)
    audio_start = frames_to_seconds(audio.source_in + overlap_start - audio.timeline_start, fps)
    audio_end = frames_to_seconds(audio.source_in + overlap_end - audio.timeline_start, fps)
    tolerance = max(0.05, frames_to_seconds(2, fps))
    if video_start < -tolerance or video_end > video_duration + tolerance:
        raise ValueError(f"XML video range exceeds media duration: {video.path}")
    if audio_start < -tolerance or audio_end > audio_duration + tolerance:
        raise ValueError(f"XML audio range exceeds media duration: {audio.path}")
    video_start = min(max(0.0, video_start), video_duration)
    video_end = min(max(video_start, video_end), video_duration)
    audio_start = min(max(0.0, audio_start), audio_duration)
    audio_end = min(max(audio_start, audio_end), audio_duration)
    if video_end <= video_start or audio_end <= audio_start:
        raise ValueError(f"XML produces an empty synchronized range: {video.name}")

    offset_frames = (
        video.timeline_start - audio.timeline_start + audio.source_in - video.source_in
    )
    return Alignment(
        source_id=video.path.stem,
        video=video.path,
        audio=audio.path,
        video_duration_s=video_duration,
        audio_duration_s=audio_duration,
        audio_offset_s=frames_to_seconds(offset_frames, fps),
        coverage_video_start_s=video_start,
        coverage_video_end_s=video_end,
        coverage_audio_start_s=audio_start,
        coverage_audio_end_s=audio_end,
        backend="pluraleyes-xml",
    )


def _extract_analysis_wav(source: Path, destination: Path, sample_rate: int) -> None:
    run_checked(
        [
            "ffmpeg", "-y", "-i", source, "-vn", "-ac", "1", "-ar", str(sample_rate),
            "-c:a", "pcm_s16le", destination,
        ],
        stage=f"extract waveform reference for {source.name}",
    )


def _read_pcm16(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as stream:
        if stream.getsampwidth() != 2 or stream.getnchannels() != 1:
            raise ValueError(f"analysis WAV must be mono PCM16: {path}")
        frames = stream.readframes(stream.getnframes())
    samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32)
    if samples.size < WAVEFORM_ANALYSIS_RATE:
        raise ValueError("waveform input is too short for reliable synchronization")
    samples -= float(samples.mean())
    scale = float(samples.std())
    if scale < 1e-6:
        raise ValueError("waveform input is silent")
    return samples / scale


def estimate_waveform_offset(reference: np.ndarray, external: np.ndarray, sample_rate: int) -> tuple[float, float]:
    """Return external-audio time at video time zero and normalized confidence."""
    size = reference.size + external.size - 1
    fft_size = 1 << (size - 1).bit_length()
    correlation = np.fft.irfft(
        np.fft.rfft(reference, fft_size) * np.fft.rfft(external[::-1], fft_size),
        fft_size,
    )[:size]
    peak_index = int(np.argmax(np.abs(correlation)))
    lag = peak_index - (external.size - 1)
    if lag < 0:
        ref_slice = reference[: min(reference.size, external.size + lag)]
        ext_slice = external[-lag : -lag + ref_slice.size]
    else:
        ext_slice = external[: min(external.size, reference.size - lag)]
        ref_slice = reference[lag : lag + ext_slice.size]
    if ref_slice.size < sample_rate:
        raise ValueError("waveform overlap is too short for reliable synchronization")
    denominator = float(np.linalg.norm(ref_slice) * np.linalg.norm(ext_slice))
    confidence = abs(float(np.dot(ref_slice, ext_slice)) / denominator) if denominator else 0.0
    return -lag / sample_rate, confidence


def alignment_from_waveforms(
    video: Path,
    audio: Path,
    *,
    min_confidence: float = WAVEFORM_MIN_CONFIDENCE,
) -> Alignment:
    video = video.resolve()
    audio = audio.resolve()
    if not video.is_file() or not audio.is_file():
        raise FileNotFoundError(f"video/audio pair does not exist: {video} / {audio}")
    with tempfile.TemporaryDirectory(prefix="video-use-sync-analysis-") as temp_name:
        temp = Path(temp_name)
        reference_wav = temp / "reference.wav"
        external_wav = temp / "external.wav"
        _extract_analysis_wav(video, reference_wav, WAVEFORM_ANALYSIS_RATE)
        _extract_analysis_wav(audio, external_wav, WAVEFORM_ANALYSIS_RATE)
        offset, confidence = estimate_waveform_offset(
            _read_pcm16(reference_wav), _read_pcm16(external_wav), WAVEFORM_ANALYSIS_RATE
        )
    video_duration = _probe_duration(video)
    audio_duration = _probe_duration(audio)
    coverage_start = max(0.0, -offset)
    coverage_end = min(video_duration, audio_duration - offset)
    if confidence < min_confidence:
        raise ValueError(
            f"waveform sync confidence {confidence:.3f} is below {min_confidence:.3f}"
        )
    if coverage_end <= coverage_start:
        raise ValueError("waveform synchronization produced no overlapping media")
    return Alignment(
        source_id=video.stem,
        video=video,
        audio=audio,
        video_duration_s=video_duration,
        audio_duration_s=audio_duration,
        audio_offset_s=offset,
        coverage_video_start_s=coverage_start,
        coverage_video_end_s=coverage_end,
        coverage_audio_start_s=coverage_start + offset,
        coverage_audio_end_s=coverage_end + offset,
        backend="waveform",
        confidence=confidence,
    )


def _atomic_output_path(destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(
        prefix=f".{destination.stem}.", suffix=destination.suffix, dir=destination.parent
    )
    os.close(handle)
    temp = Path(name)
    temp.unlink(missing_ok=True)
    return temp


def build_aligned_audio(alignment: Alignment, destination: Path) -> None:
    duration = alignment.video_duration_s
    body_duration = alignment.coverage_audio_end_s - alignment.coverage_audio_start_s
    delay_samples = round(alignment.coverage_video_start_s * DEFAULT_SAMPLE_RATE)
    temp = _atomic_output_path(destination)
    try:
        filter_graph = (
            f"[0:a]atrim=start={alignment.coverage_audio_start_s:.9f}:"
            f"duration={body_duration:.9f},asetpts=PTS-STARTPTS,"
            "aformat=sample_rates=48000:channel_layouts=mono,"
            f"adelay={delay_samples}S:all=1,apad,atrim=duration={duration:.9f}[outa]"
        )
        run_checked(
            [
                "ffmpeg", "-y", "-i", alignment.audio, "-filter_complex", filter_graph,
                "-map", "[outa]", "-c:a", "pcm_s24le", "-ar", "48000", "-ac", "1", temp,
            ],
            stage=f"align external audio for {alignment.video.name}",
        )
        rendered_duration = _probe_duration(temp)
        if abs(rendered_duration - duration) > 0.05:
            raise RuntimeError(
                f"aligned audio duration mismatch for {alignment.video.name}: "
                f"{rendered_duration:.3f}s vs {duration:.3f}s"
            )
        os.replace(temp, destination)
    finally:
        temp.unlink(missing_ok=True)


def materialize_synced_media(video: Path, aligned_audio: Path, destination: Path) -> None:
    temp = _atomic_output_path(destination)
    try:
        run_checked(
            [
                "ffmpeg", "-y", "-i", video, "-i", aligned_audio,
                "-map", "0:v:0", "-map", "1:a:0", "-map_metadata", "0",
                "-c:v", "copy", "-c:a", "pcm_s24le", "-ar", "48000", temp,
            ],
            stage=f"materialize synchronized MOV for {video.name}",
        )
        os.replace(temp, destination)
    finally:
        temp.unlink(missing_ok=True)


def _alignment_metadata(alignment: Alignment, aligned_audio: Path) -> dict[str, Any]:
    return {
        "backend": alignment.backend,
        "audio_offset_s": round(alignment.audio_offset_s, 9),
        "coverage": {
            "video_start_s": round(alignment.coverage_video_start_s, 9),
            "video_end_s": round(alignment.coverage_video_end_s, 9),
            "audio_start_s": round(alignment.coverage_audio_start_s, 9),
            "audio_end_s": round(alignment.coverage_audio_end_s, 9),
        },
        "gap_policy": "silence",
        "video": {**_media_signature(alignment.video), "duration_s": alignment.video_duration_s},
        "audio": {**_media_signature(alignment.audio), "duration_s": alignment.audio_duration_s},
        "aligned_audio": {
            **_media_signature(aligned_audio),
            "duration_s": alignment.video_duration_s,
        },
        "confidence": alignment.confidence,
    }


def _signature_matches(expected: dict[str, Any]) -> bool:
    try:
        path = Path(expected["path"])
        if not path.is_file():
            return False
        current = _media_signature(path)
        return all(
            current[key] == expected[key]
            for key in ("path", "size_bytes", "mtime_ns", "quick_sha256")
        )
    except (KeyError, OSError, TypeError):
        return False


def _manifest_matches_entry(entry: dict[str, Any]) -> bool:
    try:
        return all(
            _signature_matches(entry["alignment"][name])
            for name in ("video", "audio", "aligned_audio")
        )
    except (KeyError, TypeError):
        return False


def validate_sync_manifest(data: dict[str, Any]) -> None:
    if data.get("schema_version") != MANIFEST_VERSION:
        raise ValueError(f"sync manifest schema_version must be {MANIFEST_VERSION}")
    if data.get("gap_policy") != "silence":
        raise ValueError("sync manifest gap_policy must be silence")
    sources = data.get("sources")
    if not isinstance(sources, dict) or not sources:
        raise ValueError("sync manifest sources must be a non-empty object")
    normalized_paths: set[str] = set()
    for source_id, entry in sources.items():
        if not source_id or not isinstance(entry, dict):
            raise ValueError("sync manifest source entry is invalid")
        alignment = entry.get("alignment")
        if not isinstance(alignment, dict) or alignment.get("backend") not in {
            "pluraleyes-xml", "waveform"
        }:
            raise ValueError(f"sync manifest alignment is invalid for {source_id}")
        try:
            offset = float(alignment["audio_offset_s"])
            coverage = alignment["coverage"]
            values = [offset, *(float(coverage[key]) for key in (
                "video_start_s", "video_end_s", "audio_start_s", "audio_end_s"
            ))]
            if not all(math.isfinite(value) for value in values):
                raise ValueError
            if (
                coverage["video_start_s"] < 0
                or coverage["audio_start_s"] < 0
                or coverage["video_end_s"] <= coverage["video_start_s"]
                or coverage["audio_end_s"] <= coverage["audio_start_s"]
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"sync manifest coverage is invalid for {source_id}") from None
        video_path = os.path.normcase(os.path.abspath(alignment["video"]["path"]))
        if video_path in normalized_paths:
            raise ValueError(f"duplicate video path in sync manifest: {video_path}")
        normalized_paths.add(video_path)


def atomic_write_manifest(path: Path, data: dict[str, Any]) -> None:
    validate_sync_manifest(data)
    temp = _atomic_output_path(path)
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def load_sync_manifest(path: Path, *, require_current: bool = True) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_sync_manifest(data)
    if require_current:
        if data.get("xml") is not None and not _signature_matches(data["xml"]):
            raise ValueError("sync manifest is stale because its PluralEyes XML changed")
        stale = [source_id for source_id, entry in data["sources"].items() if not _manifest_matches_entry(entry)]
        if stale:
            raise ValueError(f"sync manifest is stale for: {', '.join(stale)}")
    return data


def find_manifest_entry(manifest: dict[str, Any], video: Path) -> dict[str, Any]:
    target = os.path.normcase(os.path.abspath(video.resolve()))
    matches = [
        entry for entry in manifest["sources"].values()
        if os.path.normcase(os.path.abspath(entry["alignment"]["video"]["path"])) == target
    ]
    if len(matches) != 1:
        raise ValueError(f"sync manifest must contain exactly one mapping for {video}; found {len(matches)}")
    return matches[0]


def prepare_alignments(
    alignments: list[Alignment],
    edit_dir: Path,
    *,
    xml_path: Path | None = None,
    fps: Fraction | None = None,
    sequence_name: str | None = None,
    materialize: bool = False,
    force: bool = False,
) -> Path:
    edit_dir = edit_dir.resolve()
    manifest_path = edit_dir / "sync_manifest.json"
    if manifest_path.exists() and not force:
        try:
            manifest = load_sync_manifest(manifest_path)
            expected_videos = {os.path.normcase(str(item.video.resolve())) for item in alignments}
            cached_videos = {
                os.path.normcase(str(Path(entry["alignment"]["video"]["path"])))
                for entry in manifest["sources"].values()
            }
            xml_matches = (
                xml_path is None and manifest.get("xml") is None
                or xml_path is not None
                and manifest.get("xml", {}).get("quick_sha256") == file_sha256(xml_path.resolve())
            )
            if expected_videos == cached_videos and xml_matches:
                if not materialize:
                    return manifest_path
                changed = False
                for source_id, entry in manifest["sources"].items():
                    materialized = entry.get("materialized_path")
                    if materialized and Path(materialized).is_file():
                        continue
                    alignment = entry["alignment"]
                    destination = edit_dir / "synced" / f"{source_id}_synced.mov"
                    materialize_synced_media(
                        Path(alignment["video"]["path"]),
                        Path(alignment["aligned_audio"]["path"]),
                        destination,
                    )
                    entry["materialized_path"] = str(destination)
                    changed = True
                if changed:
                    manifest["created_at"] = utc_now()
                    atomic_write_manifest(manifest_path, manifest)
                return manifest_path
        except (ValueError, OSError, json.JSONDecodeError):
            pass

    source_ids: set[str] = set()
    sources: dict[str, Any] = {}
    for alignment in alignments:
        if alignment.source_id in source_ids:
            raise ValueError(f"duplicate source id from video stem: {alignment.source_id}")
        source_ids.add(alignment.source_id)
        aligned_audio = edit_dir / "sync" / "audio" / f"{alignment.source_id}.wav"
        build_aligned_audio(alignment, aligned_audio)
        materialized_path: Path | None = None
        if materialize:
            materialized_path = edit_dir / "synced" / f"{alignment.source_id}_synced.mov"
            materialize_synced_media(alignment.video, aligned_audio, materialized_path)
        sources[alignment.source_id] = {
            "source_id": alignment.source_id,
            "alignment": _alignment_metadata(alignment, aligned_audio),
            "materialized_path": str(materialized_path) if materialized_path else None,
        }

    xml_metadata = None
    if xml_path is not None:
        xml_metadata = {
            **_media_signature(xml_path),
            "sequence": sequence_name,
            "fps_num": fps.numerator if fps else None,
            "fps_den": fps.denominator if fps else None,
        }
    manifest = {
        "schema_version": MANIFEST_VERSION,
        "created_at": utc_now(),
        "gap_policy": "silence",
        "xml": xml_metadata,
        "sources": sources,
    }
    atomic_write_manifest(manifest_path, manifest)
    return manifest_path


def prepare_pluraleyes_sync(
    xml_path: Path,
    edit_dir: Path,
    *,
    sequence_name: str = DEFAULT_SEQUENCE,
    materialize: bool = False,
    force: bool = False,
) -> Path:
    xml_path = xml_path.resolve()
    if not xml_path.is_file():
        raise FileNotFoundError(f"PluralEyes XML does not exist: {xml_path}")
    fps, pairs, _available = parse_pluraleyes_xml(xml_path, sequence_name=sequence_name)
    alignments = [_alignment_from_xml(video, audio, fps) for video, audio in pairs]
    return prepare_alignments(
        alignments,
        edit_dir,
        xml_path=xml_path,
        fps=fps,
        sequence_name=sequence_name,
        materialize=materialize,
        force=force,
    )


def prepare_waveform_sync(
    video: Path,
    audio: Path,
    edit_dir: Path,
    *,
    materialize: bool = False,
    force: bool = False,
    min_confidence: float = WAVEFORM_MIN_CONFIDENCE,
) -> Path:
    try:
        alignment = alignment_from_waveforms(video, audio, min_confidence=min_confidence)
    except Exception as exc:
        report = edit_dir.resolve() / "sync" / "waveform_failure.json"
        temp = _atomic_output_path(report)
        try:
            temp.write_text(
                json.dumps(
                    {
                        "created_at": utc_now(),
                        "video": str(video.resolve()),
                        "audio": str(audio.resolve()),
                        "minimum_confidence": min_confidence,
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                    indent=2,
                ) + "\n",
                encoding="utf-8",
            )
            os.replace(temp, report)
        finally:
            temp.unlink(missing_ok=True)
        raise
    return prepare_alignments(
        [alignment], edit_dir, materialize=materialize, force=force
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Synchronize external audio for video-use")
    parser.add_argument("video", type=Path, nargs="?")
    parser.add_argument("--external-audio", type=Path)
    parser.add_argument("--xml", type=Path)
    parser.add_argument("--sequence", default=DEFAULT_SEQUENCE)
    parser.add_argument("--edit-dir", type=Path, required=True)
    parser.add_argument("--materialize", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--min-confidence", type=float, default=WAVEFORM_MIN_CONFIDENCE)
    return parser


def main() -> int:
    configure_console()
    parser = build_parser()
    args = parser.parse_args()
    if args.xml:
        if args.video or args.external_audio:
            parser.error("--xml cannot be combined with video or --external-audio")
    elif not args.video or not args.external_audio:
        parser.error("provide --xml, or provide VIDEO with --external-audio")
    try:
        if args.xml:
            manifest = prepare_pluraleyes_sync(
                args.xml,
                args.edit_dir,
                sequence_name=args.sequence,
                materialize=args.materialize,
                force=args.force,
            )
        else:
            manifest = prepare_waveform_sync(
                args.video,
                args.external_audio,
                args.edit_dir,
                materialize=args.materialize,
                force=args.force,
                min_confidence=args.min_confidence,
            )
        data = load_sync_manifest(manifest)
        print(f"sync ready: {len(data['sources'])} source(s) -> {manifest}")
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
