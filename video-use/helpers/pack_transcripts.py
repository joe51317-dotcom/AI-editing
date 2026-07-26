"""Pack Transcript V2 files into a compact, human-readable markdown index."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from config import configure_console
from transcript_v2 import join_tokens, validate_transcript


def format_time(seconds: float) -> str:
    return f"{seconds:06.2f}"


def format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    return f"{minutes}m {seconds - minutes * 60:04.1f}s"


def group_into_phrases(words: list[dict], silence_threshold: float = 0.5) -> list[dict]:
    """Break V2 word/punctuation tokens on silence or speaker changes."""
    phrases: list[dict] = []
    current: list[dict] = []
    current_speaker: str | None = None
    previous_end: float | None = None

    def flush() -> None:
        nonlocal current, current_speaker
        if current:
            text = join_tokens([token["text"] for token in current])
            if text:
                phrases.append(
                    {
                        "start": current[0]["start"],
                        "end": current[-1]["end"],
                        "text": text,
                        "speaker_id": current_speaker,
                    }
                )
        current = []
        current_speaker = None

    for token in words:
        if token.get("kind") not in {"word", "punctuation"}:
            continue
        start = float(token["start"])
        end = float(token["end"])
        speaker = token.get("speaker_id")
        if current and previous_end is not None and start - previous_end >= silence_threshold:
            flush()
        if current and speaker is not None and current_speaker is not None and speaker != current_speaker:
            flush()
        if not current:
            current_speaker = speaker
        current.append(token)
        previous_end = max(previous_end or 0.0, end)
    flush()
    return phrases


def pack_one_file(json_path: Path, silence_threshold: float) -> tuple[str, float, list[dict]]:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    validate_transcript(data)
    phrases = group_into_phrases(data["words"], silence_threshold)
    return json_path.stem, float(data["source"]["duration_s"]), phrases


def render_markdown(entries: list[tuple[str, float, list[dict]]], silence_threshold: float) -> str:
    lines = [
        "# Packed transcripts",
        "",
        f"Phrase-level, grouped on silences >= {silence_threshold:.1f}s or speaker change.",
        "Use `[start-end]` ranges to address cuts in the EDL.",
        "",
    ]
    for name, duration, phrases in entries:
        lines.append(f"## {name}  (duration: {format_duration(duration)}, {len(phrases)} phrases)")
        if not phrases:
            lines.extend(["  _no speech detected_", ""])
            continue
        for phrase in phrases:
            speaker = phrase.get("speaker_id")
            speaker_tag = f" {speaker}" if speaker else ""
            lines.append(
                f"  [{format_time(phrase['start'])}-{format_time(phrase['end'])}]"
                f"{speaker_tag} {phrase['text']}"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    configure_console()
    parser = argparse.ArgumentParser(description="Pack Transcript V2 files into takes_packed.md")
    parser.add_argument("--edit-dir", type=Path, required=True)
    parser.add_argument("--silence-threshold", type=float, default=0.5)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()

    transcripts_dir = args.edit_dir.resolve() / "transcripts"
    if not transcripts_dir.is_dir():
        sys.exit(f"no transcripts directory at {transcripts_dir}")
    json_files = sorted(transcripts_dir.glob("*.json"))
    if not json_files:
        sys.exit(f"no .json files in {transcripts_dir}")

    try:
        entries = [pack_one_file(path, args.silence_threshold) for path in json_files]
    except (ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"invalid Transcript V2: {exc}")
    out_path = args.output or (args.edit_dir.resolve() / "takes_packed.md")
    out_path.write_text(render_markdown(entries, args.silence_threshold), encoding="utf-8")
    print(f"packed {len(entries)} transcripts -> {out_path}")


if __name__ == "__main__":
    main()
