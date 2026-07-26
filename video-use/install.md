---
name: video-use-install
description: Install this Windows-local video editing skill with the existing DACE speech runtime.
---

# video-use install

This fork is installed at `E:\Claude Workspace\tools\ai-editing\video-use`. It uses a dedicated
Python 3.11 environment for editing while speech recognition and optional diarization run in the
existing DACE-BRAIN environment as separate child processes. No hosted speech provider is used.

## Install

From PowerShell:

```powershell
cd 'E:\Claude Workspace\tools\ai-editing\video-use'
.\setup.ps1 -ImportDaceHfToken
.\.venv\Scripts\python.exe helpers\doctor.py
```

`-ImportDaceHfToken` copies only the existing `HF_TOKEN` value into the untracked local `.env`.
It is used only when `--diarize` is requested. The command never prints the value and does not copy
other settings.

The required local contract is documented in `.env.example`. The default DACE runtime is:

```text
E:\Claude Workspace\DACE-BRAIN\.venv\Scripts\python.exe
```

## Agent registration

Both agents must point to the same stable directory. On this workstation they are directory
junctions, not copies:

```text
C:\Users\GR\.claude\skills\video-use -> E:\Claude Workspace\tools\ai-editing\video-use
C:\Users\GR\.codex\skills\video-use  -> C:\Users\GR\.claude\skills\video-use
```

Restart the Claude Code or Codex session after creating or changing a skill junction; an already
running session may have cached skill discovery.

## Verify

```powershell
.\.venv\Scripts\python.exe helpers\doctor.py
.\.venv\Scripts\python.exe helpers\transcribe.py <video> --language zh
.\.venv\Scripts\python.exe helpers\pack_transcripts.py --edit-dir <edit-dir>
```

For multiple speakers, add `--diarize`; `--num-speakers N` is valid only with that flag. The
diarization model requires the workstation's existing Hugging Face token and accepted model terms.
If CUDA memory is exhausted, retry explicitly with `--low-memory`; this changes only the compute
type to `int8_float16`, never the model.

Manim scenes can consume a user-provided narration track. Automatic voice generation is disabled
because this workstation has no previously verified local voice generator.
