from __future__ import annotations

import json
from pathlib import Path

import pytest

from transcript_v2 import (
    TranscriptValidationError,
    archive_cache,
    assign_speakers,
    atomic_write_json,
    cache_matches,
    file_sha256,
    join_tokens,
    normalize_turns,
    normalize_words,
    validate_transcript,
)


def transcript(source_path: Path) -> dict:
    return {
        "schema_version": 2,
        "source": {
            "path": str(source_path),
            "duration_s": 2.0,
            "size_bytes": 3,
            "mtime_ns": 4,
            "quick_sha256": "abc",
            "hash_mode": "quick",
        },
        "transcription": {
            "backend": "faster-whisper",
            "model": "large-v3",
            "device": "cuda",
            "compute_type": "float16",
            "language": "zh",
        },
        "words": [],
        "speaker_turns": [],
        "events": [],
        "created_at": "2026-07-26T00:00:00Z",
    }


def test_normalize_words_clamps_sorts_and_keeps_punctuation() -> None:
    words = normalize_words(
        [
            {"word": "。", "start": 1.2, "end": 1.2},
            {"word": " 你好", "start": -0.2, "end": 0.8, "probability": "0.9"},
            {"word": "bad", "start": "nan", "end": 1},
            {"word": "swap", "start": 1.8, "end": 1.4},
            {"word": "", "start": 0, "end": 1},
        ],
        2.0,
    )
    assert [item["text"] for item in words] == ["你好", "。", "swap"]
    assert words[1]["kind"] == "punctuation"
    assert words[0]["start"] == 0
    assert [item["id"] for item in words] == [0, 1, 2]


@pytest.mark.parametrize(
    ("tokens", "expected"),
    [
        (["我", "喜歡", "剪輯", "。"], "我喜歡剪輯。"),
        (["Hello", "world", "!"], "Hello world!"),
        (["今天", "is", "good", "。"], "今天is good。"),
    ],
)
def test_join_tokens_language_aware(tokens: list[str], expected: str) -> None:
    assert join_tokens(tokens) == expected


def test_speaker_overlap_tie_and_no_match() -> None:
    words = normalize_words(
        [
            {"word": "A", "start": 1.0, "end": 2.0},
            {"word": "B", "start": 4.0, "end": 4.5},
        ],
        5.0,
    )
    turns = [
        {"start": 0.5, "end": 1.5, "speaker_id": "SPEAKER_00"},
        {"start": 1.5, "end": 2.5, "speaker_id": "SPEAKER_01"},
    ]
    assign_speakers(words, turns)
    assert words[0]["speaker_id"] == "SPEAKER_00"
    assert words[1]["speaker_id"] is None


def test_normalize_turns_removes_invalid_and_clamps() -> None:
    turns = normalize_turns(
        [
            {"start": -1, "end": 1, "speaker_id": "A"},
            {"start": 2, "end": 10, "speaker_id": "B"},
            {"start": 1, "end": 1, "speaker_id": "C"},
            {"start": 0, "end": 1, "speaker_id": ""},
        ],
        3,
    )
    assert turns == [
        {"start": 0.0, "end": 1.0, "speaker_id": "A"},
        {"start": 2.0, "end": 3, "speaker_id": "B"},
    ]


def test_validation_rejects_bad_schema_and_timestamps(tmp_path: Path) -> None:
    data = transcript(tmp_path / "a.mp4")
    data["schema_version"] = 1
    with pytest.raises(TranscriptValidationError, match="schema_version"):
        validate_transcript(data)
    data = transcript(tmp_path / "a.mp4")
    data["words"] = [{"id": 0, "text": "x", "start": 3, "end": 4, "kind": "word"}]
    with pytest.raises(TranscriptValidationError, match="out of range"):
        validate_transcript(data)


def test_cache_match_checks_source_and_settings(tmp_path: Path) -> None:
    data = transcript(tmp_path / "a.mp4")
    expected = {"model": "large-v3", "device": "cuda", "compute_type": "float16"}
    assert cache_matches(data, data["source"], expected)
    changed = dict(data["source"], mtime_ns=99)
    assert not cache_matches(data, changed, expected)
    assert not cache_matches(data, data["source"], dict(expected, compute_type="int8_float16"))


def test_atomic_write_and_archive_are_recoverable(tmp_path: Path) -> None:
    target = tmp_path / "clip.json"
    data = transcript(tmp_path / "clip.mp4")
    atomic_write_json(target, data)
    assert json.loads(target.read_text(encoding="utf-8"))["schema_version"] == 2
    backup = archive_cache(target, "legacy")
    assert backup.exists() and not target.exists()


def test_atomic_write_does_not_replace_on_validation_error(tmp_path: Path) -> None:
    target = tmp_path / "clip.json"
    target.write_text("old", encoding="utf-8")
    with pytest.raises(TranscriptValidationError):
        atomic_write_json(target, {"schema_version": 1})
    assert target.read_text(encoding="utf-8") == "old"


def test_quick_and_strong_hash_small_file_match(tmp_path: Path) -> None:
    source = tmp_path / "small.bin"
    source.write_bytes(b"abc")
    assert file_sha256(source) == file_sha256(source, strong=True)
