"""Transcript V2 schema, normalization, fingerprints, and atomic cache writes."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 2
QUICK_HASH_CHUNK = 4 * 1024 * 1024
PUNCTUATION_RE = re.compile(r"^[\W_]+$", re.UNICODE)


class TranscriptValidationError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def file_sha256(path: Path, *, strong: bool = False) -> str:
    digest = hashlib.sha256()
    size = path.stat().st_size
    with path.open("rb") as handle:
        if strong or size <= QUICK_HASH_CHUNK * 2:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        else:
            digest.update(handle.read(QUICK_HASH_CHUNK))
            handle.seek(max(0, size - QUICK_HASH_CHUNK))
            digest.update(handle.read(QUICK_HASH_CHUNK))
    return digest.hexdigest()


def source_signature(path: Path, duration_s: float, *, strong: bool = False) -> dict[str, Any]:
    resolved = path.resolve()
    stat = resolved.stat()
    return {
        "path": str(resolved),
        "duration_s": round(float(duration_s), 6),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "quick_sha256": file_sha256(resolved, strong=strong),
        "hash_mode": "strong" if strong else "quick",
    }


def is_punctuation(text: str) -> bool:
    if not text:
        return False
    return bool(PUNCTUATION_RE.match(text)) or all(
        unicodedata.category(ch).startswith(("P", "S")) for ch in text
    )


def normalize_words(raw_words: list[dict[str, Any]], duration_s: float) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for source_index, raw in enumerate(raw_words):
        text = str(raw.get("text") or raw.get("word") or "").strip()
        if not text:
            continue
        try:
            start = float(raw.get("start"))
            end = float(raw.get("end"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(start) or not math.isfinite(end):
            continue
        start = min(max(0.0, start), duration_s)
        end = min(max(0.0, end), duration_s)
        if end < start:
            start, end = end, start
        kind = "punctuation" if is_punctuation(text) else "word"
        if end <= start and kind == "word":
            continue
        probability = raw.get("probability")
        if probability is not None:
            try:
                probability = float(probability)
                if not math.isfinite(probability):
                    probability = None
            except (TypeError, ValueError):
                probability = None
        normalized.append(
            {
                "_source_index": source_index,
                "text": text,
                "start": round(start, 6),
                "end": round(end, 6),
                "kind": kind,
                "speaker_id": None,
                "probability": probability,
            }
        )
    normalized.sort(key=lambda word: (word["start"], word["end"], word["_source_index"]))
    for idx, word in enumerate(normalized):
        word.pop("_source_index", None)
        word["id"] = idx
    return normalized


def assign_speakers(words: list[dict[str, Any]], turns: list[dict[str, Any]]) -> None:
    ordered_turns = sorted(turns, key=lambda turn: (turn["start"], turn["end"], turn["speaker_id"]))
    for word in words:
        if word.get("kind") != "word":
            continue
        best: tuple[float, float, str] | None = None
        for turn in ordered_turns:
            overlap = max(0.0, min(word["end"], turn["end"]) - max(word["start"], turn["start"]))
            if overlap <= 0:
                continue
            candidate = (overlap, -float(turn["start"]), str(turn["speaker_id"]))
            if best is None or candidate > best:
                best = candidate
        word["speaker_id"] = best[2] if best else None


def normalize_turns(raw_turns: list[dict[str, Any]], duration_s: float) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_turns):
        try:
            start = float(raw.get("start"))
            end = float(raw.get("end"))
        except (TypeError, ValueError):
            continue
        speaker = str(raw.get("speaker_id") or "").strip()
        if not speaker or not math.isfinite(start) or not math.isfinite(end):
            continue
        start = min(max(0.0, start), duration_s)
        end = min(max(0.0, end), duration_s)
        if end <= start:
            continue
        turns.append(
            {
                "_source_index": index,
                "start": round(start, 6),
                "end": round(end, 6),
                "speaker_id": speaker,
            }
        )
    turns.sort(key=lambda turn: (turn["start"], turn["end"], turn["_source_index"]))
    for turn in turns:
        turn.pop("_source_index", None)
    return turns


def validate_transcript(data: dict[str, Any]) -> None:
    errors: list[str] = []
    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    source = data.get("source")
    if not isinstance(source, dict):
        errors.append("source must be an object")
        duration = 0.0
    else:
        try:
            duration = float(source.get("duration_s", 0.0))
        except (TypeError, ValueError):
            duration = 0.0
        if not math.isfinite(duration) or duration < 0:
            errors.append("source.duration_s must be finite and non-negative")
        for key in ("path", "size_bytes", "mtime_ns", "quick_sha256"):
            if key not in source:
                errors.append(f"source.{key} is required")
    transcription = data.get("transcription")
    if not isinstance(transcription, dict) or transcription.get("backend") != "faster-whisper":
        errors.append("transcription.backend must be faster-whisper")
    elif not all(key in transcription for key in ("model", "device", "compute_type", "language")):
        errors.append("transcription model, device, compute_type, and language are required")
    words = data.get("words")
    if not isinstance(words, list):
        errors.append("words must be an array")
    else:
        previous_start = -1.0
        for index, word in enumerate(words):
            if not isinstance(word, dict):
                errors.append(f"words[{index}] must be an object")
                continue
            if word.get("kind") not in {"word", "punctuation"}:
                errors.append(f"words[{index}].kind is invalid")
            try:
                start = float(word["start"])
                end = float(word["end"])
            except (KeyError, TypeError, ValueError):
                errors.append(f"words[{index}] timestamps are invalid")
                continue
            if not math.isfinite(start) or not math.isfinite(end):
                errors.append(f"words[{index}] timestamps must be finite")
            elif start < 0 or end < start or end > duration + 1e-3:
                errors.append(f"words[{index}] timestamps are out of range")
            if start < previous_start:
                errors.append(f"words[{index}] is not sorted")
            previous_start = start
    if not isinstance(data.get("speaker_turns"), list):
        errors.append("speaker_turns must be an array")
    else:
        for index, turn in enumerate(data["speaker_turns"]):
            try:
                start = float(turn["start"])
                end = float(turn["end"])
            except (KeyError, TypeError, ValueError):
                errors.append(f"speaker_turns[{index}] is invalid")
                continue
            if (
                not math.isfinite(start)
                or not math.isfinite(end)
                or start < 0
                or end <= start
                or end > duration + 1e-3
                or not str(turn.get("speaker_id") or "").strip()
            ):
                errors.append(f"speaker_turns[{index}] is out of range")
    if not isinstance(data.get("events"), list):
        errors.append("events must be an array")
    audio_alignment = data.get("audio_alignment")
    if audio_alignment is not None:
        if not isinstance(audio_alignment, dict):
            errors.append("audio_alignment must be an object or null")
        else:
            required = {"backend", "audio_offset_s", "coverage", "gap_policy", "video", "audio", "aligned_audio"}
            if not required.issubset(audio_alignment):
                errors.append("audio_alignment metadata is incomplete")
            elif audio_alignment.get("gap_policy") != "silence":
                errors.append("audio_alignment.gap_policy must be silence")
    if not isinstance(data.get("created_at"), str) or not data.get("created_at"):
        errors.append("created_at is required")
    if errors:
        raise TranscriptValidationError("; ".join(errors))


def cache_matches(
    data: dict[str, Any],
    source: dict[str, Any],
    expected_transcription: dict[str, Any],
) -> bool:
    try:
        validate_transcript(data)
    except TranscriptValidationError:
        return False
    cached_source = data["source"]
    for key in ("path", "size_bytes", "mtime_ns", "quick_sha256", "hash_mode"):
        if cached_source.get(key) != source.get(key):
            return False
    cached_tx = data["transcription"]
    for key, value in expected_transcription.items():
        if cached_tx.get(key) != value:
            return False
    return True


def archive_cache(path: Path, label: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    counter = 0
    while True:
        suffix = f".{counter}" if counter else ""
        target = path.with_name(f"{path.stem}.{label}.{stamp}{suffix}{path.suffix}")
        if not target.exists():
            path.replace(target)
            return target
        counter += 1


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    validate_transcript(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def _is_cjk_char(char: str) -> bool:
    code = ord(char)
    return (
        0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0x3040 <= code <= 0x30FF
        or 0xAC00 <= code <= 0xD7AF
    )


def join_tokens(tokens: list[str]) -> str:
    result = ""
    closing = set("，。！？；：、,.!?;:%)]}》〉」』】")
    opening = set("([{《〈「『【")
    for raw in tokens:
        token = raw.strip()
        if not token:
            continue
        if not result:
            result = token
            continue
        previous = result[-1]
        no_space = (
            token[0] in closing
            or previous in opening
            or _is_cjk_char(previous)
            or _is_cjk_char(token[0])
        )
        result += token if no_space else f" {token}"
    return result.strip()
