# Upstream provenance

- Repository: `browser-use/video-use`
- Imported commit: `92c2b34e44c205cbc2acae7f6ca7c1c219d5dd66`
- Imported: 2026-07-26
- License: MIT; the original `LICENSE` is retained unchanged.

## Local changes

This fork replaces hosted speech services with a subprocess contract to the existing DACE-BRAIN
Python 3.11 runtime. It adds Transcript V2, validated/atomic caches, local faster-whisper,
optional pyannote diarization, strict runtime/font/ffmpeg checks, sequential batch processing, and
Windows setup/skill registration documentation. Automatic voice generation is disabled.

It also adds a Premiere Pro hand-off: `helpers/export_premiere_xml.py` writes the EDL as an FCP7
sequence against the original sources, `helpers/edl.py` holds the EDL loading, validation, and
ffprobe helpers both consumers need, and `helpers/render.py` calls the exporter after a successful
render. `helpers/sync_media.py` gains `rate_from_fps`, the inverse of its existing `fps_from_rate`.

This file is documentation, not a mechanism: the subtree merge does not read it and it does not
protect anything. `SKILL.md` and `helpers/render.py` are the local edits most likely to conflict
with an upstream change. `tools/auto_update.ps1` stops and leaves `main` untouched on conflict, so
a stalled automatic update means one of those files needs a manual merge.

## Reviewing an update

Run the following from the stable `video-use` directory:

```powershell
.\tools\update_upstream.ps1 -Ref <full-commit-sha>
```

The script clones that revision into a unique system temporary directory, verifies `LICENSE` and
`SKILL.md`, and prints a no-index diff against this directory. It never writes into the fork. Review
and port upstream changes manually, then update the commit recorded above.

## Automatic updates

`tools/install_auto_update.ps1` registers a daily current-user task. The task runs
`tools/auto_update.ps1`, which starts from the latest `origin/main` in an isolated temporary
worktree, performs a Git subtree three-way merge from the last recorded upstream split, runs the
unit suite and the paid-provider scan, and pushes only when all checks pass. A conflict or failed
test leaves `main` unchanged and is recorded under `%LOCALAPPDATA%\video-use\update.log`.

The importing commit must retain these Git trailers so the first automatic merge has the correct
base:

```text
git-subtree-dir: video-use
git-subtree-split: 92c2b34e44c205cbc2acae7f6ca7c1c219d5dd66
```
