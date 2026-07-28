"""EDL loading, validation, and media probing shared by render and export.

``render.py`` turns an EDL into a video; ``export_premiere_xml.py`` turns the
same EDL into an FCP7 sequence. Both need to read the EDL, resolve its relative
paths, and ask ffprobe about the sources — so those live here rather than in
either consumer.

The probing here answers questions the render pipeline never had to ask. The
render re-encodes every segment to one output frame rate, so it does not care
whether a source is 29.97 or variable. An XML timeline points at the original
files and expresses every cut as a frame number, so source rate is load-bearing:
a wrong rate silently moves every cut.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from subprocess_utils import run_checked


# A constant-frame-rate file reports the same nominal and average rate. Real
# files drift a little because avg_frame_rate is derived from frame count over
# duration, so an exact comparison would call every file variable. 1% is wide
# enough to absorb that rounding and narrow enough to catch phone footage,
# which typically lands 2-5% below its nominal rate.
VFR_TOLERANCE = 0.01

# The pix_fmt families that can carry alpha. Substring-matching a bare 'a'
# would misread opaque formats whose names merely contain one. pal8 belongs
# here because palettes carry per-entry alpha, which is how transparent GIF
# overlays arrive.
ALPHA_PIX_FMT_PREFIXES = (
    "yuva", "ya", "rgba", "bgra", "argb", "abgr", "gbra", "ayuv", "pal8",
)


# -------- EDL loading --------------------------------------------------------


def load_edl(path: Path) -> dict:
    """Load EDL JSON as UTF-8 so Traditional Chinese Windows paths stay exact."""
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_path(maybe_path: str, base: Path) -> Path:
    """Resolve a path that may be absolute or relative to `base`."""
    p = Path(maybe_path)
    if p.is_absolute():
        return p
    return (base / p).resolve()


# -------- Media probing ------------------------------------------------------


@dataclass(frozen=True)
class MediaRate:
    """Frame-rate facts for one video file.

    `nominal` is ffprobe's r_frame_rate, the rate the container advertises.
    `average` is avg_frame_rate, frames divided by duration. They agree on
    constant-frame-rate media and diverge on variable-frame-rate media.
    """

    nominal: Fraction
    average: Fraction | None
    nb_frames: int | None
    duration_s: float

    @property
    def is_variable(self) -> bool:
        if self.average is None or self.average <= 0:
            return False
        drift = abs(self.nominal - self.average) / self.nominal
        return float(drift) > VFR_TOLERANCE


@dataclass(frozen=True)
class StreamInfo:
    """Display geometry and stream layout for one media file."""

    width: int
    height: int
    pix_fmt: str
    rotation: int
    audio_channels: int
    has_video: bool
    has_audio: bool

    @property
    def display_width(self) -> int:
        return self.height if abs(self.rotation) % 180 == 90 else self.width

    @property
    def display_height(self) -> int:
        return self.width if abs(self.rotation) % 180 == 90 else self.height

    @property
    def has_alpha(self) -> bool:
        return self.pix_fmt.startswith(ALPHA_PIX_FMT_PREFIXES)


PROBE_ENTRIES = (
    "stream=index,codec_type,r_frame_rate,avg_frame_rate,nb_frames,"
    "width,height,pix_fmt,channels"
    ":stream_tags=rotate"
    ":stream_side_data=rotation"
    ":format=duration"
)


def _probe_json(path: Path) -> dict:
    if not path.exists():
        raise RuntimeError(f"media file not found: {path}")
    result = run_checked(
        ["ffprobe", "-v", "error", "-show_entries", PROBE_ENTRIES, "-of", "json", str(path)],
        stage=f"probe {path.name}",
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ffprobe returned invalid JSON for {path}") from exc


def _parse_fraction(value: str | None) -> Fraction | None:
    """Parse ffprobe's `num/den` rate strings. Returns None for N/A and 0/0."""
    if not value or value.strip() in {"N/A", "0/0"}:
        return None
    try:
        fraction = Fraction(value.strip())
    except (ValueError, ZeroDivisionError):
        return None
    return fraction if fraction > 0 else None


def _parse_int(value: object) -> int | None:
    if value is None or str(value).strip() in {"", "N/A"}:
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def _first_video_stream(data: dict, path: Path) -> dict:
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            return stream
    raise RuntimeError(f"no video stream in {path}")


def probe_rate(video: Path) -> MediaRate:
    """Read nominal rate, average rate, frame count, and duration."""
    data = _probe_json(video)
    stream = _first_video_stream(data, video)

    nominal = _parse_fraction(stream.get("r_frame_rate"))
    if nominal is None:
        raise RuntimeError(
            f"ffprobe could not determine a frame rate for {video}. "
            "Pass --fps to set the sequence rate manually."
        )

    duration_raw = data.get("format", {}).get("duration")
    try:
        duration = float(duration_raw)
    except (TypeError, ValueError):
        raise RuntimeError(f"ffprobe returned an invalid duration for {video}")
    if not math.isfinite(duration) or duration <= 0:
        raise RuntimeError(f"media duration must be positive: {video}")

    return MediaRate(
        nominal=nominal,
        average=_parse_fraction(stream.get("avg_frame_rate")),
        nb_frames=_parse_int(stream.get("nb_frames")),
        duration_s=duration,
    )


def probe_streams(path: Path) -> StreamInfo:
    """Read display geometry, rotation, pixel format, and channel count."""
    data = _probe_json(path)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = [s for s in streams if s.get("codec_type") == "audio"]

    rotation = 0
    if video is not None:
        candidates = [item.get("rotation") for item in video.get("side_data_list", [])]
        candidates.append(video.get("tags", {}).get("rotate"))
        rotation = next((int(v) for v in candidates if v is not None), 0)

    return StreamInfo(
        width=(_parse_int(video.get("width")) or 0) if video else 0,
        height=(_parse_int(video.get("height")) or 0) if video else 0,
        pix_fmt=(video.get("pix_fmt") or "") if video else "",
        rotation=rotation,
        audio_channels=(_parse_int(audio[0].get("channels")) or 0) if audio else 0,
        has_video=video is not None,
        has_audio=bool(audio),
    )


def duration_in_frames(rate: MediaRate) -> tuple[int, bool]:
    """Total frames of a source, and whether the number is exact.

    Prefer the container's own frame count. When it is absent, fall back to
    duration times rate, which is an estimate — callers surface that so the
    user knows why a clip might not trim as far as expected.
    """
    if rate.nb_frames is not None and rate.nb_frames > 0:
        return rate.nb_frames, True
    return max(1, round(rate.duration_s * float(rate.nominal))), False


# -------- EDL validation -----------------------------------------------------


def validate_edl(edl: dict, edit_dir: Path) -> None:
    """Reject EDLs that would produce a broken timeline.

    Every failure here is one the user would otherwise meet inside Premiere as
    offline media or a cut in the wrong place, where the cause is invisible.
    """
    sources = edl.get("sources")
    if not isinstance(sources, dict) or not sources:
        raise ValueError("EDL must define a non-empty 'sources' object")

    ranges = edl.get("ranges")
    if not isinstance(ranges, list) or not ranges:
        raise ValueError("EDL must define at least one range")

    missing = [
        str(resolve_path(value, edit_dir))
        for value in sources.values()
        if not resolve_path(value, edit_dir).exists()
    ]
    if missing:
        listed = "\n  ".join(missing)
        raise ValueError(f"EDL source files do not exist:\n  {listed}")

    for index, item in enumerate(ranges):
        label = f"range {index}"
        name = item.get("source")
        if name not in sources:
            raise ValueError(f"{label} references unknown source {name!r}")
        try:
            start = float(item["start"])
            end = float(item["end"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"{label} must have numeric 'start' and 'end'")
        if not math.isfinite(start) or not math.isfinite(end):
            raise ValueError(f"{label} has a non-finite start or end")
        if start < 0:
            raise ValueError(f"{label} starts before zero ({start})")
        if end <= start:
            raise ValueError(f"{label} ends at or before it starts ({start} → {end})")
        punch = item.get("punch", 1.0)
        try:
            punch_value = float(punch)
        except (TypeError, ValueError):
            raise ValueError(f"{label} has a non-numeric punch {punch!r}")
        if punch_value < 1.0:
            raise ValueError(f"{label} has punch {punch_value} below 1.0")


def validate_ranges_within_media(
    edl: dict,
    edit_dir: Path,
    durations: dict[str, float],
) -> None:
    """Reject cuts that run past the end of their source."""
    for index, item in enumerate(edl["ranges"]):
        name = item["source"]
        end = float(item["end"])
        limit = durations[name]
        if end > limit + 0.001:
            raise ValueError(
                f"range {index} ends at {end:.3f}s but {name} is only {limit:.3f}s long"
            )
