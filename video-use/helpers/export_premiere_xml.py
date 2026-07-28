"""Export an EDL as an FCP7 XML sequence Premiere Pro can import and re-edit.

The rendered ``final.mp4`` is finished — you cannot move a cut inside it. This
writes the same edit as a timeline that points at the *original* camera files,
so every cut can be dragged, extended, replaced, or thrown away in Premiere.

What travels: the cuts, the punch-in framing, the animation overlays, the
external-microphone audio, and the editor's reasoning as timeline markers.
Subtitles ride alongside as an .srt rather than as XML text generators, because
Premiere's own caption track is editable and imported generators are not.

What does not travel: the colour grade. video-use grades with ffmpeg filter
strings and Premiere has no equivalent to import them into, so the grade is
recorded in the clip names and left for the user to apply.

Usage:
    python helpers/export_premiere_xml.py <edl.json> -o <out.xml>
    python helpers/export_premiere_xml.py <edl.json> -o <out.xml> --fps 30
    python helpers/export_premiere_xml.py <edl.json> -o <out.xml> --no-punch
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from xml.dom import minidom

from config import configure_console
from edl import (
    MediaRate,
    StreamInfo,
    duration_in_frames,
    load_edl,
    probe_rate,
    probe_streams,
    resolve_path,
    validate_edl,
    validate_ranges_within_media,
)
from sync_media import find_manifest_entry, load_sync_manifest, rate_from_fps


# Decimal frame rates a user might type. FCP7 stores rates as an integer plus
# an NTSC flag, so 23.976 has to become 24000/1001 before it can be written;
# passing the decimal through would produce a rate no timebase can express.
CLI_FPS_ALIASES = {
    "23.976": Fraction(24000, 1001),
    "23.98": Fraction(24000, 1001),
    "29.97": Fraction(30000, 1001),
    "59.94": Fraction(60000, 1001),
    "119.88": Fraction(120000, 1001),
}

# Premiere shows a 4K clip in a 1080 sequence at its native pixel size, which
# reads as a 2x punch-in, so every clip carries an explicit scale rather than
# relying on the import default. A clip that already matches the sequence gets
# no filter at all.
#
# PENDING: the exact percentage basis is confirmed against
# tests/fixtures/premiere_reference.xml. Until that fixture lands, this is the
# documented FCP7 reading of scale as a percentage of native size.
BASIC_MOTION_SCALE_MAX = 1000


@dataclass
class FileRegistry:
    """Assigns one id per physical file and defines each one exactly once.

    FCP7 XML expects a file to be described in full on first reference and
    referenced by bare id afterwards. Re-describing the same path under a new
    id makes Premiere treat one camera file as many separate assets.
    """

    _ids: dict[Path, str] = field(default_factory=dict)
    _defined: set[str] = field(default_factory=set)

    def id_for(self, path: Path) -> tuple[str, bool]:
        """Return (file id, whether this call must emit the full definition)."""
        resolved = path.resolve()
        if resolved not in self._ids:
            self._ids[resolved] = f"file-{len(self._ids) + 1}"
        file_id = self._ids[resolved]
        first_use = file_id not in self._defined
        self._defined.add(file_id)
        return file_id, first_use


@dataclass(frozen=True)
class SourceProbe:
    path: Path
    rate: MediaRate
    streams: StreamInfo
    total_frames: int
    frames_exact: bool


@dataclass(frozen=True)
class PlacedRange:
    """One EDL range resolved onto the output timeline."""

    index: int
    source_name: str
    source: SourceProbe
    seq_start: int
    seq_end: int
    src_in: int
    src_out: int
    punch: float
    audio_path: Path
    audio_is_external: bool
    beat: str
    reason: str


# -------- Frame rate resolution ----------------------------------------------


def coerce_fps(value: object) -> Fraction:
    """Read a frame rate from a string or number, snapping the NTSC decimals."""
    text = str(value).strip()
    if text in CLI_FPS_ALIASES:
        return CLI_FPS_ALIASES[text]
    return Fraction(text)


def parse_cli_fps(value: str) -> Fraction:
    try:
        return coerce_fps(value)
    except (ValueError, ZeroDivisionError):
        raise argparse.ArgumentTypeError(f"could not read a frame rate from {value!r}")


_STREAM_CACHE: dict[Path, StreamInfo] = {}


def streams_for(path: Path) -> StreamInfo:
    """probe_streams with memoisation — one ffprobe call per physical file."""
    key = path.resolve()
    if key not in _STREAM_CACHE:
        _STREAM_CACHE[key] = probe_streams(key)
    return _STREAM_CACHE[key]


def resolve_sequence_fps(
    probes: dict[str, SourceProbe],
    override: Fraction | None,
) -> Fraction:
    """Pick the sequence rate, refusing the cases v1 cannot place accurately.

    Mixed rates need FCP7's mixedratesoffset to stay frame-accurate, and a
    variable-rate file has no single rate to convert seconds against. Both are
    rejected instead of warned about: a timeline that looks right but sits a
    few frames off every cut is worse than one that refuses to be written.
    """
    variable = [name for name, p in probes.items() if p.rate.is_variable]
    rates = {name: p.rate.nominal for name, p in probes.items()}
    distinct = sorted(set(rates.values()))

    if override is not None:
        if variable:
            print(f"warning: --fps overrides variable-frame-rate sources: {', '.join(sorted(variable))}")
        if len(distinct) > 1:
            print(f"warning: --fps overrides {len(distinct)} different source frame rates")
        return override

    if variable:
        listed = "\n  ".join(
            f"{name}: nominal {float(probes[name].rate.nominal):.3f} fps, "
            f"average {float(probes[name].rate.average or 0):.3f} fps"
            for name in sorted(variable)
        )
        raise ValueError(
            "variable-frame-rate sources cannot carry frame-accurate cuts:\n  "
            f"{listed}\n"
            "Convert them to constant frame rate, or pass --fps to place cuts anyway."
        )

    if len(distinct) > 1:
        listed = "\n  ".join(
            f"{name}: {float(rate):.3f} fps" for name, rate in sorted(rates.items())
        )
        raise ValueError(
            "sources do not share one frame rate, which this exporter cannot "
            f"place accurately:\n  {listed}\n"
            "Conform them to one rate, or pass --fps to pick the sequence rate."
        )

    return distinct[0]


# -------- Timeline placement -------------------------------------------------


def place_ranges(
    edl: dict,
    edit_dir: Path,
    probes: dict[str, SourceProbe],
    fps: Fraction,
    sync_manifest: dict | None,
) -> list[PlacedRange]:
    """Lay every range onto the output timeline.

    Output positions accumulate in seconds and convert to frames once, so each
    clip starts exactly where the previous one ended — no gaps, no overlaps, no
    drift accumulating across a long edit. Individual cut points can still land
    up to half a frame from the requested second; that is inherent to putting
    a decimal time onto a frame grid.
    """
    placed: list[PlacedRange] = []
    elapsed = 0.0

    for index, item in enumerate(edl["ranges"]):
        name = item["source"]
        probe = probes[name]
        start = float(item["start"])
        end = float(item["end"])

        seq_start = round(elapsed * float(fps))
        elapsed += end - start
        seq_end = round(elapsed * float(fps))

        src_in = round(start * float(fps))
        # Source length must match timeline length or Premiere reads a speed
        # change. v1 holds one rate for everything, so this is exact.
        src_out = src_in + (seq_end - seq_start)
        if src_out > probe.total_frames:
            raise ValueError(
                f"range {index} needs frames up to {src_out} but {name} has only "
                f"{probe.total_frames}"
            )

        audio_path = probe.path
        audio_is_external = False
        if sync_manifest is not None:
            entry = find_manifest_entry(sync_manifest, probe.path)
            audio_path = Path(entry["alignment"]["aligned_audio"]["path"])
            audio_is_external = True

        placed.append(
            PlacedRange(
                index=index,
                source_name=name,
                source=probe,
                seq_start=seq_start,
                seq_end=seq_end,
                src_in=src_in,
                src_out=src_out,
                punch=float(item.get("punch", 1.0)),
                audio_path=audio_path,
                audio_is_external=audio_is_external,
                beat=str(item.get("beat") or ""),
                reason=str(item.get("reason") or item.get("quote") or ""),
            )
        )

    return placed


def sequence_geometry(probes: dict[str, SourceProbe], override: str | None) -> tuple[int, int]:
    """Delivery frame size for the sequence.

    Defaults to what render.py produces: 1080 on the short edge, oriented by
    the first source. Getting this wrong makes every scale value wrong, so it
    is written into the XML explicitly rather than left for Premiere to infer.
    """
    if override:
        try:
            width, height = (int(part) for part in override.lower().split("x", 1))
        except ValueError:
            raise ValueError(f"--sequence-size must look like 1920x1080, got {override!r}")
        if width <= 0 or height <= 0:
            raise ValueError(f"--sequence-size must be positive, got {override!r}")
        return width, height

    first = next(iter(probes.values()))
    portrait = first.streams.display_height > first.streams.display_width
    return (1080, 1920) if portrait else (1920, 1080)


def scale_percent(streams: StreamInfo, seq_w: int, seq_h: int, punch: float) -> float:
    """Percentage that fills the frame, multiplied by the punch-in factor.

    ffmpeg punches by cropping at source resolution and then scaling to the
    delivery size. Premiere reaches the same framing by scaling up from the
    fitted size, so the punch factor multiplies the fit rather than replacing
    it — writing `punch * 100` would blow a 4K clip far past the frame.
    """
    if not streams.display_width or not streams.display_height:
        return 100.0 * punch
    fit = min(seq_w / streams.display_width, seq_h / streams.display_height)
    return min(fit * 100.0 * punch, float(BASIC_MOTION_SCALE_MAX))


# -------- XML building -------------------------------------------------------


def path_url(path: Path) -> str:
    """FCP7 pathurl. Percent-encoded so Traditional Chinese paths survive."""
    encoded = urllib.parse.quote(path.resolve().as_posix(), safe="/:")
    return f"file://localhost/{encoded.lstrip('/')}"


def _rate_element(parent: ET.Element, timebase: int, ntsc: bool) -> None:
    rate = ET.SubElement(parent, "rate")
    ET.SubElement(rate, "timebase").text = str(timebase)
    ET.SubElement(rate, "ntsc").text = "TRUE" if ntsc else "FALSE"


def _file_element(
    parent: ET.Element,
    registry: FileRegistry,
    path: Path,
    timebase: int,
    ntsc: bool,
    duration_frames: int,
    streams: StreamInfo | None,
) -> None:
    """Emit a file reference, defining it in full only on first use."""
    file_id, first_use = registry.id_for(path)
    node = ET.SubElement(parent, "file", id=file_id)
    if not first_use:
        return

    ET.SubElement(node, "name").text = path.name
    ET.SubElement(node, "pathurl").text = path_url(path)
    _rate_element(node, timebase, ntsc)
    # Full media length, so Premiere knows how far past each cut the footage
    # runs and lets the user drag a cut open.
    ET.SubElement(node, "duration").text = str(duration_frames)

    timecode = ET.SubElement(node, "timecode")
    ET.SubElement(timecode, "string").text = "00:00:00:00"
    ET.SubElement(timecode, "frame").text = "0"
    ET.SubElement(timecode, "displayformat").text = "NDF"
    _rate_element(timecode, timebase, ntsc)

    media = ET.SubElement(node, "media")
    if streams is not None and streams.has_video:
        video = ET.SubElement(media, "video")
        characteristics = ET.SubElement(video, "samplecharacteristics")
        _rate_element(characteristics, timebase, ntsc)
        ET.SubElement(characteristics, "width").text = str(streams.display_width)
        ET.SubElement(characteristics, "height").text = str(streams.display_height)
        ET.SubElement(characteristics, "pixelaspectratio").text = "square"
    if streams is None or streams.has_audio:
        audio = ET.SubElement(media, "audio")
        characteristics = ET.SubElement(audio, "samplecharacteristics")
        ET.SubElement(characteristics, "depth").text = "16"
        ET.SubElement(characteristics, "samplerate").text = "48000"
        channels = streams.audio_channels if streams is not None else 2
        ET.SubElement(audio, "channelcount").text = str(max(1, min(channels or 2, 2)))


def _link_elements(parent: ET.Element, members: list[tuple[str, str, int, int]]) -> None:
    """Bind a range's video and audio clipitems so they move and trim together.

    Without these, Premiere treats picture and sound as unrelated clips and the
    first trim silently desyncs them.
    """
    for clip_id, mediatype, trackindex, clipindex in members:
        link = ET.SubElement(parent, "link")
        ET.SubElement(link, "linkclipref").text = clip_id
        ET.SubElement(link, "mediatype").text = mediatype
        ET.SubElement(link, "trackindex").text = str(trackindex)
        ET.SubElement(link, "clipindex").text = str(clipindex)


def _punch_filter(parent: ET.Element, scale: float) -> None:
    node = ET.SubElement(parent, "filter")
    effect = ET.SubElement(node, "effect")
    ET.SubElement(effect, "name").text = "Basic Motion"
    ET.SubElement(effect, "effectid").text = "basic"
    ET.SubElement(effect, "effectcategory").text = "motion"
    ET.SubElement(effect, "effecttype").text = "motion"
    ET.SubElement(effect, "mediatype").text = "video"
    parameter = ET.SubElement(effect, "parameter")
    ET.SubElement(parameter, "parameterid").text = "scale"
    ET.SubElement(parameter, "name").text = "Scale"
    ET.SubElement(parameter, "valuemin").text = "0"
    ET.SubElement(parameter, "valuemax").text = str(BASIC_MOTION_SCALE_MAX)
    ET.SubElement(parameter, "value").text = f"{scale:.4f}".rstrip("0").rstrip(".")


def build_xml(
    edl: dict,
    edit_dir: Path,
    placed: list[PlacedRange],
    fps: Fraction,
    seq_w: int,
    seq_h: int,
    sequence_name: str,
    overlays: list[dict],
    with_markers: bool,
    with_punch: bool,
) -> ET.Element:
    timebase, ntsc = rate_from_fps(fps)
    registry = FileRegistry()
    grade = str(edl.get("grade") or "")

    xmeml = ET.Element("xmeml", version="5")
    sequence = ET.SubElement(xmeml, "sequence", id=sequence_name)
    ET.SubElement(sequence, "name").text = sequence_name
    ET.SubElement(sequence, "duration").text = str(placed[-1].seq_end if placed else 0)
    _rate_element(sequence, timebase, ntsc)

    media = ET.SubElement(sequence, "media")
    video = ET.SubElement(media, "video")

    video_format = ET.SubElement(video, "format")
    characteristics = ET.SubElement(video_format, "samplecharacteristics")
    _rate_element(characteristics, timebase, ntsc)
    ET.SubElement(characteristics, "width").text = str(seq_w)
    ET.SubElement(characteristics, "height").text = str(seq_h)
    ET.SubElement(characteristics, "pixelaspectratio").text = "square"
    ET.SubElement(characteristics, "fielddominance").text = "none"

    # How many audio tracks the timeline needs: the widest stereo pair any
    # range asks for. Mono sources sit on A1 alone.
    audio_track_count = max(
        (min(max(item.source.streams.audio_channels, 0), 2) for item in placed),
        default=0,
    )
    if any(item.audio_is_external for item in placed):
        audio_track_count = max(audio_track_count, 1)

    v1 = ET.SubElement(video, "track")
    v2 = ET.SubElement(video, "track") if overlays else None
    audio_media = ET.SubElement(media, "audio")
    audio_tracks = [ET.SubElement(audio_media, "track") for _ in range(audio_track_count)]

    for position, item in enumerate(placed, start=1):
        duration = item.seq_end - item.seq_start
        video_clip_id = f"clipitem-v-{item.index}"
        members: list[tuple[str, str, int, int]] = [(video_clip_id, "video", 1, position)]

        channels = min(max(item.source.streams.audio_channels, 0), 2)
        if item.audio_is_external:
            channels = max(channels, 1)
        for channel in range(1, min(channels, audio_track_count) + 1):
            members.append((f"clipitem-a{channel}-{item.index}", "audio", channel, position))

        label = f"{item.beat} " if item.beat else ""
        clip_name = f"{label}{item.source.path.name}"
        if grade:
            clip_name = f"{clip_name} [grade: {grade}]"

        clip = ET.SubElement(v1, "clipitem", id=video_clip_id)
        ET.SubElement(clip, "name").text = clip_name
        ET.SubElement(clip, "duration").text = str(item.source.total_frames)
        _rate_element(clip, timebase, ntsc)
        ET.SubElement(clip, "start").text = str(item.seq_start)
        ET.SubElement(clip, "end").text = str(item.seq_end)
        ET.SubElement(clip, "in").text = str(item.src_in)
        ET.SubElement(clip, "out").text = str(item.src_out)
        _file_element(
            clip, registry, item.source.path, timebase, ntsc,
            item.source.total_frames, item.source.streams,
        )
        source_track = ET.SubElement(clip, "sourcetrack")
        ET.SubElement(source_track, "mediatype").text = "video"
        ET.SubElement(source_track, "trackindex").text = "1"

        if with_punch:
            scale = scale_percent(item.source.streams, seq_w, seq_h, item.punch)
            if abs(scale - 100.0) > 0.0001:
                _punch_filter(clip, scale)

        _link_elements(clip, members)

        # The aligned WAV shares its video's time zero and duration, so it uses
        # the same in/out frames and the same total length.
        audio_streams = streams_for(item.audio_path) if item.audio_is_external else item.source.streams
        audio_frames = item.source.total_frames
        for channel in range(1, min(channels, audio_track_count) + 1):
            audio_clip = ET.SubElement(
                audio_tracks[channel - 1], "clipitem", id=f"clipitem-a{channel}-{item.index}"
            )
            ET.SubElement(audio_clip, "name").text = item.audio_path.name
            ET.SubElement(audio_clip, "duration").text = str(audio_frames)
            _rate_element(audio_clip, timebase, ntsc)
            ET.SubElement(audio_clip, "start").text = str(item.seq_start)
            ET.SubElement(audio_clip, "end").text = str(item.seq_end)
            ET.SubElement(audio_clip, "in").text = str(item.src_in)
            ET.SubElement(audio_clip, "out").text = str(item.src_out)
            _file_element(
                audio_clip, registry, item.audio_path, timebase, ntsc,
                audio_frames, audio_streams,
            )
            audio_source_track = ET.SubElement(audio_clip, "sourcetrack")
            ET.SubElement(audio_source_track, "mediatype").text = "audio"
            ET.SubElement(audio_source_track, "trackindex").text = str(channel)
            _link_elements(audio_clip, members)

    for position, overlay in enumerate(overlays, start=1):
        overlay_path = resolve_path(overlay["file"], edit_dir)
        start = round(float(overlay["start_in_output"]) * float(fps))
        length = round(float(overlay["duration"]) * float(fps))
        clip = ET.SubElement(v2, "clipitem", id=f"clipitem-ov-{position}")
        ET.SubElement(clip, "name").text = overlay_path.name
        ET.SubElement(clip, "duration").text = str(length)
        _rate_element(clip, timebase, ntsc)
        ET.SubElement(clip, "start").text = str(start)
        ET.SubElement(clip, "end").text = str(start + length)
        ET.SubElement(clip, "in").text = "0"
        ET.SubElement(clip, "out").text = str(length)
        _file_element(
            clip, registry, overlay_path, timebase, ntsc, length, streams_for(overlay_path),
        )
        overlay_source_track = ET.SubElement(clip, "sourcetrack")
        ET.SubElement(overlay_source_track, "mediatype").text = "video"
        ET.SubElement(overlay_source_track, "trackindex").text = "1"

    if with_markers:
        for item in placed:
            if not item.beat and not item.reason:
                continue
            marker = ET.SubElement(sequence, "marker")
            ET.SubElement(marker, "name").text = item.beat or f"cut {item.index}"
            ET.SubElement(marker, "comment").text = item.reason
            ET.SubElement(marker, "in").text = str(item.seq_start)
            ET.SubElement(marker, "out").text = "-1"

    return xmeml


# -------- Subtitle hand-off --------------------------------------------------


def resolve_subtitles(edl: dict, edit_dir: Path, enabled: bool) -> Path | None:
    """The subtitle file this render actually used, if any.

    Follows the EDL's own path first so a session with custom subtitles does
    not silently hand over a stale master.srt from an earlier cut.
    """
    if not enabled:
        return None
    declared = edl.get("subtitles")
    if declared:
        path = resolve_path(declared, edit_dir)
        return path if path.exists() else None
    fallback = edit_dir / "master.srt"
    return fallback if fallback.exists() else None


# -------- Export -------------------------------------------------------------


def export(
    edl_path: Path,
    out_path: Path,
    fps_override: Fraction | None = None,
    sequence_size: str | None = None,
    sequence_name: str | None = None,
    with_markers: bool = True,
    with_punch: bool = True,
    with_subtitles: bool = True,
) -> Path:
    edl = load_edl(edl_path)
    edit_dir = edl_path.parent
    validate_edl(edl, edit_dir)

    probes: dict[str, SourceProbe] = {}
    for name, value in edl["sources"].items():
        path = resolve_path(value, edit_dir)
        rate = probe_rate(path)
        streams = streams_for(path)
        if streams.audio_channels > 2:
            print(
                f"warning: {name} has {streams.audio_channels} audio channels; the "
                "timeline carries the first two and drops the rest"
            )
        total_frames, exact = duration_in_frames(rate)
        if not exact:
            print(
                f"warning: {name} does not report a frame count; its length is "
                "estimated, so trimming may stop slightly short of the real end"
            )
        probes[name] = SourceProbe(path, rate, streams, total_frames, exact)

    validate_ranges_within_media(
        edl, edit_dir, {name: p.rate.duration_s for name, p in probes.items()}
    )

    fps = resolve_sequence_fps(probes, fps_override)
    render_fps = edl.get("fps")
    if render_fps is not None and coerce_fps(render_fps) != fps:
        print(
            f"warning: the EDL renders at {render_fps} fps but the sources are "
            f"{float(fps):.3f} fps. The XML describes the sources, so its timeline "
            "will not match the rendered video's length. Set the EDL 'fps' to the "
            "source rate to keep them aligned."
        )

    sync_manifest = None
    if edl.get("sync_manifest"):
        sync_manifest = load_sync_manifest(resolve_path(edl["sync_manifest"], edit_dir))

    placed = place_ranges(edl, edit_dir, probes, fps, sync_manifest)
    seq_w, seq_h = sequence_geometry(probes, sequence_size)

    overlays = list(edl.get("overlays") or [])
    for overlay in overlays:
        overlay_path = resolve_path(overlay["file"], edit_dir)
        if not overlay_path.exists():
            raise ValueError(f"overlay file does not exist: {overlay_path}")
        overlay_rate = probe_rate(overlay_path)
        declared = float(overlay["duration"])
        if overlay_rate.duration_s + 0.001 < declared:
            raise ValueError(
                f"overlay {overlay_path.name} is {overlay_rate.duration_s:.3f}s but the "
                f"EDL places {declared:.3f}s of it"
            )
        if not streams_for(overlay_path).has_alpha:
            print(
                f"warning: overlay {overlay_path.name} has no alpha channel and will "
                "cover the picture underneath it"
            )
        end = float(overlay["start_in_output"]) + declared
        timeline_end = placed[-1].seq_end / float(fps)
        if end > timeline_end + 0.001:
            print(
                f"warning: overlay {overlay_path.name} ends at {end:.3f}s, past the "
                f"{timeline_end:.3f}s timeline"
            )

    name = sequence_name or edit_dir.resolve().parent.name or "video-use edit"
    xmeml = build_xml(
        edl, edit_dir, placed, fps, seq_w, seq_h, name, overlays, with_markers, with_punch,
    )

    # Encoding is declared in the prolog, not just used for the bytes, so a
    # Traditional Chinese clip name survives the round trip into Premiere.
    pretty = minidom.parseString(ET.tostring(xmeml, encoding="utf-8")).toprettyxml(
        indent="  ", encoding="utf-8"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Write beside the target then rename, so an interrupted export cannot
    # leave half a file where a valid one used to be.
    temp_path = out_path.with_name(out_path.name + ".tmp")
    temp_path.write_bytes(pretty)
    os.replace(temp_path, out_path)

    subtitles = resolve_subtitles(edl, edit_dir, with_subtitles)
    if subtitles is not None:
        paired = out_path.with_suffix(".srt")
        if subtitles.resolve() != paired.resolve():
            shutil.copyfile(subtitles, paired)
        print(f"subtitles → {paired.name} (import in Premiere's Text panel)")

    timebase, ntsc = rate_from_fps(fps)
    print(
        f"premiere xml → {out_path.name}  "
        f"({len(placed)} clip(s), {len(overlays)} overlay(s), {seq_w}x{seq_h} @ "
        f"{'NTSC ' if ntsc else ''}{timebase})"
    )
    return out_path


def export_or_clear(edl_path: Path, out_path: Path, **kwargs) -> Path | None:
    """Export, or remove a stale XML and report why it could not be written.

    A leftover XML sitting next to a freshly rendered video reads as its
    companion. Deleting it is the honest outcome when the export fails.
    """
    try:
        return export(edl_path, out_path, **kwargs)
    except Exception as exc:
        out_path.unlink(missing_ok=True)
        out_path.with_suffix(".srt").unlink(missing_ok=True)
        print(f"warning: no Premiere XML written ({exc})")
        return None


def main() -> None:
    configure_console()
    ap = argparse.ArgumentParser(description="Export an EDL as FCP7 XML for Premiere Pro")
    ap.add_argument("edl", type=Path, help="Path to edl.json")
    ap.add_argument("-o", "--output", type=Path, required=True, help="Output .xml path")
    ap.add_argument(
        "--fps", type=parse_cli_fps, default=None,
        help="Sequence frame rate. Default: detected from the sources. Also "
             "overrides the mixed-rate and variable-rate refusals.",
    )
    ap.add_argument(
        "--sequence-size", default=None, metavar="WxH",
        help="Sequence frame size. Default: 1080 on the short edge, matching render.py.",
    )
    ap.add_argument("--sequence-name", default=None, help="Sequence name shown in Premiere")
    ap.add_argument("--no-markers", action="store_true", help="Skip the editor-reasoning markers")
    ap.add_argument(
        "--no-punch", action="store_true",
        help="Skip all scale filters, including the fit-to-frame scale",
    )
    ap.add_argument("--no-subtitles", action="store_true", help="Do not copy an .srt alongside")
    args = ap.parse_args()

    edl_path = args.edl.resolve()
    if not edl_path.exists():
        sys.exit(f"edl not found: {edl_path}")

    try:
        export(
            edl_path,
            args.output.resolve(),
            fps_override=args.fps,
            sequence_size=args.sequence_size,
            sequence_name=args.sequence_name,
            with_markers=not args.no_markers,
            with_punch=not args.no_punch,
            with_subtitles=not args.no_subtitles,
        )
    except (ValueError, RuntimeError) as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
