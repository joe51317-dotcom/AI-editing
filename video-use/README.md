# video-use — DACE local fork

A conversation-driven video editing skill for Claude Code and Codex. It keeps the upstream pack,
timeline, grade, EDL, subtitle, animation, and render workflows, while speech recognition runs
locally through the existing DACE-BRAIN `faster-whisper` runtime.

## Local architecture

```text
video-use/.venv: orchestration, pack, timeline, grade, render, tests
        |
        +-- DACE child process: faster-whisper large-v3
        +-- optional fresh child process: pyannote diarization
                            |
                      Transcript V2 JSON
```

The two ML stages never run concurrently. Batch mode uses one model process and processes files
sequentially, which is deliberate for the workstation's 8 GB GPU.

## Setup and health check

```powershell
cd 'E:\Claude Workspace\tools\ai-editing\video-use'
.\setup.ps1 -ImportDaceHfToken
.\.venv\Scripts\python.exe helpers\doctor.py
```

See [install.md](install.md) for registration and [SKILL.md](SKILL.md) for the editing workflow.

## Main commands

```powershell
# Local word-level transcript
.\.venv\Scripts\python.exe helpers\transcribe.py video.mp4 --language zh

# Optional anonymous speaker labels
.\.venv\Scripts\python.exe helpers\transcribe.py interview.mp4 --diarize --num-speakers 2

# Sequential batch; one ASR model load
.\.venv\Scripts\python.exe helpers\transcribe_batch.py footage --language zh

# PluralEyes XML: build aligned WAV files without copying 4K video
.\.venv\Scripts\python.exe helpers\sync_media.py --xml 'footage\sync.xml' --edit-dir 'footage\edit'
.\.venv\Scripts\python.exe helpers\transcribe_batch.py footage --sync-xml 'footage\sync.xml' --language zh

# Optional Premiere hand-off: stream-copy video into MOV with 48 kHz PCM audio
.\.venv\Scripts\python.exe helpers\sync_media.py --xml 'footage\sync.xml' --edit-dir 'footage\edit' --materialize

# Without XML, correlate one camera track with one external recording locally
.\.venv\Scripts\python.exe helpers\sync_media.py video.mov --external-audio recorder.wav --edit-dir 'footage\edit'

# Pack, inspect, and render
.\.venv\Scripts\python.exe helpers\pack_transcripts.py --edit-dir footage\edit
.\.venv\Scripts\python.exe helpers\timeline_view.py video.mp4 10 20
.\.venv\Scripts\python.exe helpers\render.py footage\edit\edl.json -o final.mp4 --build-subtitles
```

`--low-memory` selects `int8_float16` explicitly after a CUDA out-of-memory error. It does not
silently downgrade the model. `--strong-cache-hash` hashes the whole source; the default hashes
metadata plus the first and last blocks for speed.

PluralEyes synchronization always selects the exact `Synced Sequence Replaced` sequence unless
`--sequence`/`--sync-sequence` explicitly names another one. The source media stays untouched.
Uncovered heads and tails are silence, never camera audio. Add `"sync_manifest":
"sync_manifest.json"` to `edl.json` so rendering uses the same aligned WAV files as transcription.

## Capability differences

- Speech stays on this workstation; there is no cloud speech fallback.
- Transcript V2 contains words, punctuation, anonymous speaker turns, and source/cache metadata.
- The local recognizer does not invent laughter, applause, or other audio events.
- Diarization is optional and requires the existing Hugging Face account/model authorization.
- Manim accepts an existing narration file; automatic voice generation is disabled.

## Upstream

Imported from `browser-use/video-use` under the MIT License. See [UPSTREAM.md](UPSTREAM.md) and
[LICENSE](LICENSE). The update tool downloads into a temporary directory and shows a diff; it never
overwrites this fork.
