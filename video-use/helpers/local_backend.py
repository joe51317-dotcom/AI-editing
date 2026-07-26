"""DACE runtime backend for local faster-whisper ASR and pyannote diarization.

This file is invoked as a subprocess through VIDEO_USE_ASR_PYTHON. Keeping the
heavy ML imports here prevents video-use's editing environment from inheriting
DACE-BRAIN's CUDA dependency graph.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any


PYANNOTE_MODEL = "pyannote/speaker-diarization-3.1"


def _read_jobs(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data.get("jobs"), list) or not data["jobs"]:
        raise ValueError("job file must contain a non-empty jobs array")
    return data


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def doctor(args: argparse.Namespace) -> int:
    packages = {}
    for package in ("faster-whisper", "ctranslate2", "torch", "pyannote.audio", "soundfile"):
        try:
            packages[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            packages[package] = None

    missing = [name for name in ("faster-whisper", "ctranslate2") if not packages[name]]
    cuda_available = None
    cuda_device = None
    try:
        import torch

        cuda_available = bool(torch.cuda.is_available())
        if cuda_available:
            cuda_device = torch.cuda.get_device_name(0)
    except Exception:
        cuda_available = False

    model_cached = False
    model_error = None
    try:
        model_path = Path(args.model)
        if model_path.exists():
            model_cached = True
        else:
            from huggingface_hub import snapshot_download

            snapshot_download(repo_id=args.model, local_files_only=True)
            model_cached = True
    except Exception as exc:
        model_error = f"{type(exc).__name__}: {exc}"

    errors = []
    if missing:
        errors.append(f"missing packages: {', '.join(missing)}")
    if args.device == "cuda" and not cuda_available:
        errors.append("CUDA is not available to the DACE runtime")
    if not model_cached:
        errors.append(f"ASR model is not cached locally: {args.model} ({model_error})")
    if args.diarize:
        if not packages.get("pyannote.audio") or not packages.get("soundfile"):
            errors.append("pyannote.audio and soundfile are required for --diarize")
        token = os.environ.get("HF_TOKEN", "").strip()
        if not token:
            errors.append("HF_TOKEN is required for --diarize")
        elif packages.get("pyannote.audio"):
            try:
                from pyannote.audio import Pipeline

                pipeline = Pipeline.from_pretrained(PYANNOTE_MODEL, token=token)
                if pipeline is None:
                    raise RuntimeError("model access was not granted")
                del pipeline
            except Exception as exc:
                errors.append(f"pyannote model is unavailable or unauthorized: {type(exc).__name__}: {exc}")

    print(
        json.dumps(
            {
                "ok": not errors,
                "python": sys.executable,
                "packages": packages,
                "cuda_available": cuda_available,
                "cuda_device": cuda_device,
                "model": args.model,
                "model_cached": model_cached,
                "errors": errors,
            },
            ensure_ascii=False,
        )
    )
    # The orchestrator consumes the structured report and decides whether to
    # stop. Returning zero keeps stdout available instead of hiding it behind a
    # CalledProcessError wrapper.
    return 0


def run_asr(job_file: Path) -> int:
    spec = _read_jobs(job_file)
    model_name = str(spec["model"])
    device = str(spec.get("device") or "cuda")
    compute_type = str(spec.get("compute_type") or "float16")
    language = spec.get("language") or None

    from faster_whisper import WhisperModel

    try:
        model = WhisperModel(model_name, device=device, compute_type=compute_type)
        for job in spec["jobs"]:
            segments, info = model.transcribe(
                str(job["input"]),
                language=language,
                word_timestamps=True,
                condition_on_previous_text=False,
                vad_filter=False,
            )
            words: list[dict[str, Any]] = []
            for segment in segments:
                for word in segment.words or []:
                    words.append(
                        {
                            "text": word.word,
                            "start": word.start,
                            "end": word.end,
                            "probability": word.probability,
                        }
                    )
            _write_json(
                Path(job["output"]),
                {
                    "language": getattr(info, "language", language),
                    "language_probability": getattr(info, "language_probability", None),
                    "words": words,
                },
            )
    except Exception as exc:
        message = str(exc)
        if "out of memory" in message.lower() or "cuda" in message.lower() and "alloc" in message.lower():
            raise RuntimeError(
                f"GPU out of memory while running {model_name}. Retry with --low-memory. Original: {message}"
            ) from exc
        raise
    return 0


def _load_audio_tensor(path: Path) -> dict[str, Any]:
    import numpy as np
    import soundfile as sf
    import torch

    audio, sample_rate = sf.read(str(path), dtype="float32", always_2d=False)
    if sample_rate != 16000:
        raise RuntimeError(f"diarization WAV sample rate must be 16000, got {sample_rate}")
    if audio.ndim == 1:
        audio = audio[None, :]
    else:
        audio = audio.T
    return {
        "waveform": torch.from_numpy(np.ascontiguousarray(audio)),
        "sample_rate": sample_rate,
    }


def _annotation_from_output(output: Any) -> Any:
    if hasattr(output, "exclusive_speaker_diarization"):
        return output.exclusive_speaker_diarization
    if hasattr(output, "speaker_diarization"):
        return output.speaker_diarization
    return output


def run_diarization(job_file: Path) -> int:
    spec = _read_jobs(job_file)
    token = os.environ.get("HF_TOKEN", "").strip()
    if not token:
        raise RuntimeError("HF_TOKEN is required for local pyannote diarization")

    import torch
    from pyannote.audio import Pipeline

    pipeline = Pipeline.from_pretrained(PYANNOTE_MODEL, token=token)
    if str(spec.get("device") or "cuda") == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is not available for pyannote")
        pipeline.to(torch.device("cuda"))
        if hasattr(pipeline, "segmentation_batch_size"):
            pipeline.segmentation_batch_size = 16
        if hasattr(pipeline, "embedding_batch_size"):
            pipeline.embedding_batch_size = 16

    for job in spec["jobs"]:
        kwargs: dict[str, int] = {}
        hint = job.get("num_speakers")
        if hint is not None:
            kwargs["max_speakers"] = int(hint)
        output = pipeline(_load_audio_tensor(Path(job["input"])), **kwargs)
        annotation = _annotation_from_output(output)
        turns = [
            {
                "start": round(float(turn.start), 6),
                "end": round(float(turn.end), 6),
                "speaker_id": str(speaker),
            }
            for turn, _track, speaker in annotation.itertracks(yield_label=True)
            if float(turn.end) > float(turn.start)
        ]
        if not turns:
            raise RuntimeError(f"pyannote returned no speaker turns for {job['input']}")
        _write_json(Path(job["output"]), {"speaker_turns": turns})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local ML backend for video-use")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor_parser = sub.add_parser("doctor")
    doctor_parser.add_argument("--model", required=True)
    doctor_parser.add_argument("--device", default="cuda")
    doctor_parser.add_argument("--diarize", action="store_true")

    asr_parser = sub.add_parser("asr")
    asr_parser.add_argument("--job-file", type=Path, required=True)

    diarize_parser = sub.add_parser("diarize")
    diarize_parser.add_argument("--job-file", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "doctor":
            return doctor(args)
        if args.command == "asr":
            return run_asr(args.job_file)
        return run_diarization(args.job_file)
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        if os.environ.get("VIDEO_USE_DEBUG") == "1":
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
