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
