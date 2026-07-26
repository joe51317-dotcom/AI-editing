"""Shared configuration and font discovery for the local video-use fork."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = "Systran/faster-whisper-large-v3"
DEFAULT_FONT_CANDIDATES = (
    Path(r"C:\Windows\Fonts\NotoSansTC-VF.ttf"),
    Path(r"C:\Windows\Fonts\msjh.ttc"),
    Path(r"C:\Windows\Fonts\msjhbd.ttc"),
    Path("/System/Library/Fonts/PingFang.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
)


def configure_console() -> None:
    """Use UTF-8 for Windows CLI output without changing redirected streams."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        values[key] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class VideoUseConfig:
    repo_root: Path
    asr_python: Path | None
    model: str
    device: str
    compute_type: str
    font_path: Path | None
    hf_token: str
    env: dict[str, str]

    def child_env(self) -> dict[str, str]:
        child = os.environ.copy()
        child.update(self.env)
        child["PYTHONIOENCODING"] = "utf-8"
        child["PYTHONUTF8"] = "1"
        if self.hf_token:
            child["HF_TOKEN"] = self.hf_token
        return child


def load_config(env_path: Path | None = None) -> VideoUseConfig:
    """Load repo-local .env, with process environment taking precedence."""
    env_path = env_path or (REPO_ROOT / ".env")
    file_values = _parse_env_file(env_path)
    merged = file_values.copy()
    for key in (
        "VIDEO_USE_ASR_PYTHON",
        "VIDEO_USE_ASR_MODEL",
        "VIDEO_USE_DEVICE",
        "VIDEO_USE_COMPUTE_TYPE",
        "VIDEO_USE_FONT",
        "HF_TOKEN",
    ):
        if key in os.environ:
            merged[key] = os.environ[key]

    asr_raw = merged.get("VIDEO_USE_ASR_PYTHON", "").strip()
    font_raw = merged.get("VIDEO_USE_FONT", "").strip()
    return VideoUseConfig(
        repo_root=REPO_ROOT,
        asr_python=Path(asr_raw).expanduser() if asr_raw else None,
        model=merged.get("VIDEO_USE_ASR_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        device=merged.get("VIDEO_USE_DEVICE", "cuda").strip() or "cuda",
        compute_type=merged.get("VIDEO_USE_COMPUTE_TYPE", "float16").strip() or "float16",
        font_path=Path(font_raw).expanduser() if font_raw else None,
        hf_token=merged.get("HF_TOKEN", "").strip(),
        env=merged,
    )


def resolve_font_path(config: VideoUseConfig | None = None) -> Path:
    config = config or load_config()
    candidates = ([config.font_path] if config.font_path else []) + list(DEFAULT_FONT_CANDIDATES)
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate.resolve()
    raise RuntimeError(
        "No Traditional Chinese font found. Set VIDEO_USE_FONT in video-use/.env "
        "to a valid .ttf/.ttc file."
    )


def font_family_name(font_path: Path) -> str:
    from PIL import ImageFont

    font = ImageFont.truetype(str(font_path), 24)
    family, _style = font.getname()
    if not family:
        raise RuntimeError(f"Could not resolve font family name: {font_path}")
    return family


def validate_cjk_font(font_path: Path) -> None:
    """Verify the font maps representative Traditional Chinese code points."""
    from fontTools.ttLib import TTCollection, TTFont
    from PIL import ImageFont

    required = {ord(char) for char in "繁體中文字幕"}
    fonts: list[TTFont]
    collection: TTCollection | None = None
    if font_path.suffix.lower() == ".ttc":
        collection = TTCollection(str(font_path), lazy=True)
        fonts = collection.fonts
    else:
        fonts = [TTFont(str(font_path), lazy=True)]
    try:
        if not any(required.issubset(set(font.getBestCmap())) for font in fonts):
            raise RuntimeError(f"Font is missing required Traditional Chinese glyphs: {font_path}")
    finally:
        if collection is not None:
            collection.close()
        else:
            fonts[0].close()

    font = ImageFont.truetype(str(font_path), 32)
    mask = font.getmask("繁體中文字幕")
    if mask.getbbox() is None:
        raise RuntimeError(f"Font produced an empty Traditional Chinese glyph mask: {font_path}")
