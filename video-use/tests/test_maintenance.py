from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_license_and_upstream_pin_present() -> None:
    assert "MIT License" in (ROOT / "LICENSE").read_text(encoding="utf-8")
    upstream = (ROOT / "UPSTREAM.md").read_text(encoding="utf-8")
    assert "92c2b34e44c205cbc2acae7f6ca7c1c219d5dd66" in upstream


def test_paid_provider_paths_are_absent() -> None:
    forbidden = ("eleven" + "labs", "xi-" + "api-key", "api." + "eleven" + "labs.io")
    hits: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in {".venv", ".git", "__pycache__"} for part in path.parts):
            continue
        try:
            content = path.read_text(encoding="utf-8").lower()
        except (UnicodeDecodeError, OSError):
            continue
        if any(term in content for term in forbidden):
            hits.append(str(path.relative_to(ROOT)))
    assert hits == []


def test_update_review_script_has_no_local_copy_operation() -> None:
    script = (ROOT / "tools" / "update_upstream.ps1").read_text(encoding="utf-8")
    assert "git diff --no-index" in script
    assert "Copy-Item" not in script
    assert "Move-Item" not in script


def test_auto_update_is_gated_by_tests_scan_and_isolated_worktree() -> None:
    script = (ROOT / "tools" / "auto_update.ps1").read_text(encoding="utf-8")
    for required in ("worktree", "pytest", "Paid-provider path reappeared", "git-subtree-split"):
        assert required in script
    assert script.index("pytest") < script.index("'push'")
