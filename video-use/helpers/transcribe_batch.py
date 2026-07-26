"""Sequential local-GPU batch transcription with one ASR model load."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from config import configure_console
from transcribe import TranscriptionOptions, add_common_arguments, transcribe_many


VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".mp3", ".wav", ".m4a", ".flac"}


def find_media(directory: Path) -> list[Path]:
    return sorted(
        path for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in VIDEO_EXTS
    )


def main() -> int:
    configure_console()
    parser = argparse.ArgumentParser(description="Batch-transcribe media locally with one GPU worker")
    parser.add_argument("videos_dir", type=Path)
    parser.add_argument("--edit-dir", type=Path, help="Default: <videos_dir>/edit")
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Compatibility option; local GPU mode requires 1",
    )
    add_common_arguments(parser)
    args = parser.parse_args()
    if args.workers != 1:
        parser.error("local GPU transcription requires --workers 1 to avoid model duplication and OOM")
    if args.num_speakers is not None and not args.diarize:
        parser.error("--num-speakers requires --diarize")

    videos_dir = args.videos_dir.resolve()
    if not videos_dir.is_dir():
        parser.error(f"not a directory: {videos_dir}")
    media = find_media(videos_dir)
    if not media:
        parser.error(f"no supported media found in {videos_dir}")
    edit_dir = (args.edit_dir or (videos_dir / "edit")).resolve()
    options = TranscriptionOptions(
        language=args.language,
        diarize=args.diarize,
        num_speakers=args.num_speakers,
        model=args.model,
        device=args.device,
        compute_type=args.compute_type,
        low_memory=args.low_memory,
        force=args.force,
        strong_cache_hash=args.strong_cache_hash,
    )
    try:
        outputs = transcribe_many(media, edit_dir, options)
        print(f"ready: {len(outputs)} transcript(s) in {edit_dir / 'transcripts'}")
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
