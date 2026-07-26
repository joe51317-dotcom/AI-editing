"""Transcribe local media with the existing DACE faster-whisper runtime.

Heavy ML dependencies run in a separate process. Output is validated
Transcript V2 JSON in <edit_dir>/transcripts/<source_stem>.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config import VideoUseConfig, configure_console, load_config
from doctor import run_doctor
from subprocess_utils import run_checked
from sync_media import (
    DEFAULT_SEQUENCE,
    find_manifest_entry,
    load_sync_manifest,
    prepare_pluraleyes_sync,
)
from transcript_v2 import (
    SCHEMA_VERSION,
    archive_cache,
    assign_speakers,
    atomic_write_json,
    cache_matches,
    normalize_words,
    normalize_turns,
    source_signature,
    utc_now,
    validate_transcript,
)


@dataclass(frozen=True)
class TranscriptionOptions:
    language: str | None = None
    diarize: bool = False
    num_speakers: int | None = None
    model: str | None = None
    device: str | None = None
    compute_type: str | None = None
    low_memory: bool = False
    force: bool = False
    strong_cache_hash: bool = False
    sync_xml: Path | None = None
    sync_manifest: Path | None = None
    sync_sequence: str = DEFAULT_SEQUENCE


@dataclass
class PendingMedia:
    video: Path
    asr_input: Path
    out_path: Path
    source: dict[str, Any]
    settings: dict[str, Any]
    existing_label: str | None
    audio_alignment: dict[str, Any] | None = None
    raw_asr_path: Path | None = None
    wav_path: Path | None = None
    diarization_path: Path | None = None


def _probe_duration(video: Path) -> float:
    result = run_checked(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            video,
        ],
        stage=f"probe duration for {video.name}",
    )
    try:
        duration = float(result.stdout.strip())
    except ValueError as exc:
        raise RuntimeError(f"ffprobe returned an invalid duration for {video}: {result.stdout!r}") from exc
    if duration <= 0:
        raise RuntimeError(f"media duration must be positive: {video}")
    return duration


def _extract_diarization_wav(video: Path, destination: Path) -> None:
    run_checked(
        [
            "ffmpeg",
            "-y",
            "-i",
            video,
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            destination,
        ],
        stage=f"extract 16kHz diarization audio for {video.name}",
    )


def _effective_settings(config: VideoUseConfig, options: TranscriptionOptions) -> dict[str, Any]:
    device = options.device or config.device
    compute_type = options.compute_type or config.compute_type
    if options.low_memory:
        compute_type = "int8_float16" if device == "cuda" else "int8"
    return {
        "backend": "faster-whisper",
        "model": options.model or config.model,
        "device": device,
        "compute_type": compute_type,
        "requested_language": options.language,
        "word_timestamps": True,
        "diarization": options.diarize,
        "num_speakers_hint": options.num_speakers,
        "audio_events": False,
    }


def _existing_cache_label(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None, "invalid"
    if not isinstance(data, dict):
        return None, "invalid"
    if data.get("schema_version") == SCHEMA_VERSION:
        return data, "stale"
    if isinstance(data.get("words"), list):
        return data, "legacy"
    return data, "invalid"


def _write_job_file(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _alignment_fingerprint(alignment: dict[str, Any]) -> str:
    payload = json.dumps(alignment, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def transcribe_many(
    videos: list[Path],
    edit_dir: Path,
    options: TranscriptionOptions,
    *,
    verbose: bool = True,
) -> list[Path]:
    if options.num_speakers is not None and not options.diarize:
        raise ValueError("--num-speakers requires --diarize")
    if options.num_speakers is not None and options.num_speakers < 1:
        raise ValueError("--num-speakers must be at least 1")
    if options.sync_xml is not None and options.sync_manifest is not None:
        raise ValueError("--sync-xml cannot be combined with --sync-manifest")

    sync_manifest: dict[str, Any] | None = None
    if options.sync_xml is not None:
        manifest_path = prepare_pluraleyes_sync(
            options.sync_xml,
            edit_dir,
            sequence_name=options.sync_sequence,
        )
        sync_manifest = load_sync_manifest(manifest_path)
    elif options.sync_manifest is not None:
        sync_manifest = load_sync_manifest(options.sync_manifest.resolve())

    config = load_config()
    settings = _effective_settings(config, options)
    report = run_doctor(
        diarize=options.diarize,
        model=settings["model"],
        device=settings["device"],
    )
    if not report["ok"]:
        raise RuntimeError("video-use doctor failed:\n- " + "\n- ".join(report["errors"]))
    if config.asr_python is None:
        raise RuntimeError("VIDEO_USE_ASR_PYTHON is not configured")

    transcripts_dir = edit_dir / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    pending: list[PendingMedia] = []

    for raw_video in videos:
        video = raw_video.resolve()
        if not video.is_file():
            raise FileNotFoundError(f"media not found: {video}")
        duration = _probe_duration(video)
        source = source_signature(video, duration, strong=options.strong_cache_hash)
        asr_input = video
        audio_alignment = None
        item_settings = dict(settings)
        if sync_manifest is not None:
            entry = find_manifest_entry(sync_manifest, video)
            audio_alignment = entry["alignment"]
            asr_input = Path(audio_alignment["aligned_audio"]["path"])
            item_settings["audio_alignment_sha256"] = _alignment_fingerprint(audio_alignment)
        out_path = transcripts_dir / f"{video.stem}.json"
        cached, label = _existing_cache_label(out_path)
        if not options.force and cached is not None and cache_matches(cached, source, item_settings):
            if verbose:
                print(f"cached: {out_path.name}")
            outputs.append(out_path)
            continue
        pending.append(PendingMedia(
            video=video,
            asr_input=asr_input,
            out_path=out_path,
            source=source,
            settings=item_settings,
            existing_label=label,
            audio_alignment=audio_alignment,
        ))

    if not pending:
        return outputs

    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="video-use-") as temp_name:
        temp_dir = Path(temp_name)
        for index, item in enumerate(pending):
            item.raw_asr_path = temp_dir / f"asr-{index:04d}.json"

        asr_job = temp_dir / "asr-jobs.json"
        _write_job_file(
            asr_job,
            {
                "model": settings["model"],
                "device": settings["device"],
                "compute_type": settings["compute_type"],
                "language": options.language,
                "jobs": [
                    {"input": str(item.asr_input), "output": str(item.raw_asr_path)}
                    for item in pending
                ],
            },
        )
        if verbose:
            print(f"local ASR: {len(pending)} file(s), one model load", flush=True)
        run_checked(
            [config.asr_python, Path(__file__).with_name("local_backend.py"), "asr", "--job-file", asr_job],
            stage="local faster-whisper ASR",
            env=config.child_env(),
            secrets=(config.hf_token,),
        )

        if options.diarize:
            diarization_jobs = []
            for index, item in enumerate(pending):
                item.wav_path = temp_dir / f"diarize-{index:04d}.wav"
                item.diarization_path = temp_dir / f"diarize-{index:04d}.json"
                _extract_diarization_wav(item.asr_input, item.wav_path)
                diarization_jobs.append(
                    {
                        "input": str(item.wav_path),
                        "output": str(item.diarization_path),
                        "num_speakers": options.num_speakers,
                    }
                )
            diarization_job = temp_dir / "diarization-jobs.json"
            _write_job_file(
                diarization_job,
                {"device": settings["device"], "jobs": diarization_jobs},
            )
            if verbose:
                print("ASR process complete; starting pyannote in a fresh process", flush=True)
            run_checked(
                [
                    config.asr_python,
                    Path(__file__).with_name("local_backend.py"),
                    "diarize",
                    "--job-file",
                    diarization_job,
                ],
                stage="local pyannote diarization",
                env=config.child_env(),
                secrets=(config.hf_token,),
            )

        for item in pending:
            if item.raw_asr_path is None:
                raise RuntimeError("internal error: missing ASR output path")
            raw_asr = json.loads(item.raw_asr_path.read_text(encoding="utf-8"))
            words = normalize_words(raw_asr.get("words", []), item.source["duration_s"])
            turns: list[dict[str, Any]] = []
            if options.diarize:
                if item.diarization_path is None:
                    raise RuntimeError("internal error: missing diarization output path")
                diarization = json.loads(item.diarization_path.read_text(encoding="utf-8"))
                turns = normalize_turns(
                    diarization.get("speaker_turns", []), item.source["duration_s"]
                )
                assign_speakers(words, turns)
            transcript = {
                "schema_version": SCHEMA_VERSION,
                "source": item.source,
                "transcription": {
                    **item.settings,
                    "language": raw_asr.get("language"),
                    "language_probability": raw_asr.get("language_probability"),
                },
                "words": words,
                "speaker_turns": turns,
                "events": [],
                "audio_alignment": item.audio_alignment,
                "created_at": utc_now(),
            }
            validate_transcript(transcript)
            if item.out_path.exists():
                archive_cache(item.out_path, item.existing_label or "stale")
            atomic_write_json(item.out_path, transcript)
            outputs.append(item.out_path)
            if verbose:
                print(f"saved: {item.out_path.name} ({len(words)} tokens)")

    if verbose:
        print(f"completed in {time.perf_counter() - started:.1f}s")
    return outputs


def transcribe_one(
    video: Path,
    edit_dir: Path,
    *,
    language: str | None = None,
    diarize: bool = False,
    num_speakers: int | None = None,
    model: str | None = None,
    device: str | None = None,
    compute_type: str | None = None,
    low_memory: bool = False,
    force: bool = False,
    strong_cache_hash: bool = False,
    sync_xml: Path | None = None,
    sync_manifest: Path | None = None,
    sync_sequence: str = DEFAULT_SEQUENCE,
    verbose: bool = True,
) -> Path:
    outputs = transcribe_many(
        [video],
        edit_dir,
        TranscriptionOptions(
            language=language,
            diarize=diarize,
            num_speakers=num_speakers,
            model=model,
            device=device,
            compute_type=compute_type,
            low_memory=low_memory,
            force=force,
            strong_cache_hash=strong_cache_hash,
            sync_xml=sync_xml,
            sync_manifest=sync_manifest,
            sync_sequence=sync_sequence,
        ),
        verbose=verbose,
    )
    return outputs[0]


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--language", help="ISO language code; omit for auto-detect")
    parser.add_argument("--diarize", action="store_true", help="Enable local pyannote speaker diarization")
    parser.add_argument("--num-speakers", type=int, help="Optional maximum speaker-count hint; requires --diarize")
    parser.add_argument("--model", help="Override VIDEO_USE_ASR_MODEL")
    parser.add_argument("--device", choices=("cuda", "cpu"), help="Override VIDEO_USE_DEVICE")
    parser.add_argument("--compute-type", help="Override VIDEO_USE_COMPUTE_TYPE")
    parser.add_argument("--low-memory", action="store_true", help="Use int8_float16 on CUDA or int8 on CPU")
    parser.add_argument("--force", action="store_true", help="Ignore a valid cache and transcribe again")
    parser.add_argument("--strong-cache-hash", action="store_true", help="Hash the complete media file")
    parser.add_argument("--sync-xml", type=Path, help="PluralEyes XML used to align external audio")
    parser.add_argument("--sync-manifest", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--sync-sequence", default=DEFAULT_SEQUENCE, help="Exact PluralEyes sequence name")


def main() -> int:
    configure_console()
    parser = argparse.ArgumentParser(description="Transcribe media locally into Transcript V2")
    parser.add_argument("video", type=Path)
    parser.add_argument("--edit-dir", type=Path, help="Default: <video_parent>/edit")
    add_common_arguments(parser)
    args = parser.parse_args()
    if args.num_speakers is not None and not args.diarize:
        parser.error("--num-speakers requires --diarize")
    if args.sync_xml and args.sync_manifest:
        parser.error("--sync-xml cannot be combined with --sync-manifest")
    video = args.video.resolve()
    edit_dir = (args.edit_dir or (video.parent / "edit")).resolve()
    try:
        transcribe_one(
            video,
            edit_dir,
            language=args.language,
            diarize=args.diarize,
            num_speakers=args.num_speakers,
            model=args.model,
            device=args.device,
            compute_type=args.compute_type,
            low_memory=args.low_memory,
            force=args.force,
            strong_cache_hash=args.strong_cache_hash,
            sync_xml=args.sync_xml,
            sync_manifest=args.sync_manifest,
            sync_sequence=args.sync_sequence,
        )
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
