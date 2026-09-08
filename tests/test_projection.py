"""Tests for scripts/project_cua_skill.py + verify_projection.py (design doc v2 §44)."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import project_cua_skill as projection  # noqa: E402
import verify_projection  # noqa: E402


def make_repo(tmp_path: Path) -> Path:
    """A scratch repo sharing the real lock + raw upstream source."""
    root = tmp_path / "repo"
    (root / "upstream").mkdir(parents=True)
    shutil.copy(ROOT / "upstream" / "cua.lock.json", root / "upstream" / "cua.lock.json")
    shutil.copytree(
        ROOT / "upstream" / "source" / "cua-driver",
        root / "upstream" / "source" / "cua-driver",
    )
    (root / "upstream" / "compatibility.json").write_text(json.dumps({
        "candidate": {"version": "0.24.0", "tag": "cua-driver-rs-v0.24.0",
                      "commit": "4b3396d9fe4bd3cf723b0eb8db83c18a8764b520"},
        "verified": [],
        "unsupported": [],
    }), encoding="utf-8")
    return root


def projected_and_raw(root: Path):
    projection.project(root)
    skill = root / "skills" / "cua-driver"
    source = root / "upstream" / "source" / "cua-driver"
    return skill, source


class TestProjection:
    def test_projected_skill_body_matches_upstream(self, tmp_path):
        """projected SKILL.md == normalized frontmatter + notice + transport
        + upstream body VERBATIM (nothing else may differ)."""
        root = make_repo(tmp_path)
        skill, source = projected_and_raw(root)
        upstream = (source / "SKILL.md").read_text(encoding="utf-8")
        projected = (skill / "SKILL.md").read_text(encoding="utf-8")
        _, upstream_body = projection.parse_upstream_frontmatter(upstream)
        # strip generated frontmatter + notice + transport section
        after_frontmatter = projected.split("---\n", 2)[2]
        body = after_frontmatter.split(projection.TRANSPORT_SECTION, 1)[1]
        assert body.lstrip("\n") == upstream_body.lstrip("\n")

    def test_only_declared_transforms_change_content(self, tmp_path):
        root = make_repo(tmp_path)
        skill, source = projected_and_raw(root)
        # WINDOWS.md: exactly the declared installer-block replacement.
        raw = (source / "WINDOWS.md").read_text(encoding="utf-8")
        assert (skill / "WINDOWS.md").read_text(encoding="utf-8") == (
            projection.transform_windows_md(raw)
        )

    def test_companion_files_are_byte_exact(self, tmp_path):
        root = make_repo(tmp_path)
        skill, source = projected_and_raw(root)
        for name in ("MACOS.md", "LINUX.md", "BROWSER.md", "RECORDING.md", "EMBEDDING.md"):
            assert (skill / name).read_bytes() == (source / name).read_bytes(), name

    def test_projection_fails_when_expected_upstream_block_changes(self, tmp_path):
        root = make_repo(tmp_path)
        windows = root / "upstream" / "source" / "cua-driver" / "WINDOWS.md"
        text = windows.read_text(encoding="utf-8").replace(
            "irm https://cua.ai/driver/install.ps1 | iex",
            "SomeOtherInstallerCommand",
        )
        windows.write_text(text, encoding="utf-8")
        # Simulate a legitimately re-pinned upstream: update the lock hash so
        # the failure comes from the transform's fail-closed block matching.
        lock_path = root / "upstream" / "cua.lock.json"
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        lock["files"]["WINDOWS.md"]["sha256"] = projection.sha256_hex(
            windows.read_bytes()
        )
        lock_path.write_text(json.dumps(lock, indent=2), encoding="utf-8")
        with pytest.raises(projection.ProjectionError, match="installer block not found"):
            projection.project(root)

    def test_projection_fails_on_unknown_upstream_file(self, tmp_path):
        root = make_repo(tmp_path)
        (root / "upstream" / "source" / "cua-driver" / "HISTORY.md").write_text(
            "unexpected", encoding="utf-8"
        )
        with pytest.raises(projection.ProjectionError, match="file set changed"):
            projection.project(root)

    def test_projection_digest_is_stable(self, tmp_path):
        root1 = make_repo(tmp_path / "a")
        root2 = make_repo(tmp_path / "b")
        report1 = projection.project(root1)
        report2 = projection.project(root2)
        assert report1["projectionDigest"] == report2["projectionDigest"]
        # …and it changes when the projected content changes.
        skill = root1 / "skills" / "cua-driver" / "LINUX.md"
        skill.write_bytes(skill.read_bytes() + b"\nmanual edit\n")
        manual_digest = projection.projection_digest(
            {
                p.name: p.read_bytes()
                for p in (root1 / "skills" / "cua-driver").iterdir()
                if p.is_file()
            }
        )
        assert manual_digest != report1["projectionDigest"]

    def test_generated_notice_and_frontmatter_present(self, tmp_path):
        root = make_repo(tmp_path)
        skill, _ = projected_and_raw(root)
        text = (skill / "SKILL.md").read_text(encoding="utf-8")
        assert "GENERATED FILE" in text and "project_cua_skill.py" in text
        assert 'upstream-commit: "4b3396d9fe4bd3cf723b0eb8db83c18a8764b520"' in text
        assert 'projection: "agent-plugins"' in text


class TestProjectionVerification:
    def test_clean_projection_passes_all_checks(self, tmp_path):
        root = make_repo(tmp_path)
        projection.project(root)
        failed = [c.name for c in verify_projection.run_checks(root) if not c.ok]
        assert failed == []

    def test_projection_change_invalidates_verification_and_receipt(self, tmp_path):
        root = make_repo(tmp_path)
        report = projection.project(root)
        # A receipt bound to an older projection digest.
        (root / "upstream" / "compatibility.json").write_text(json.dumps({
            "candidate": {"version": "0.24.0", "tag": "t", "commit": "4b3396d"},
            "verified": [{
                "version": "0.24.0",
                "upstreamCommit": "4b3396d9fe4bd3cf723b0eb8db83c18a8764b520",
                "skillSource": "libs/cua-driver/rust/Skills/cua-driver",
                "projectionMode": "raw-skill-normalized",
                "projectionDigest": "sha256:" + "0" * 64,
                "platform": "windows-x86_64",
            }],
            "unsupported": [],
        }), encoding="utf-8")
        failed = {
            c.name: c.detail
            for c in verify_projection.run_checks(root) if not c.ok
        }
        assert any("receipt valid" in name and "projectionDigest" in detail
                   for name, detail in failed.items())
        # A manual edit to the skill tree also breaks regeneration equality.
        skill = root / "skills" / "cua-driver" / "SKILL.md"
        skill.write_text(skill.read_text(encoding="utf-8") + "\nmanual edit\n", encoding="utf-8")
        failed = {c.name for c in verify_projection.run_checks(root) if not c.ok}
        assert any("matches regeneration" in name for name in failed)
        assert report["projectionDigest"].startswith("sha256:")

    def test_upstream_portable_mode_requires_zero_content_transforms(self, tmp_path):
        root = make_repo(tmp_path)
        projection.project(root)
        proj_path = root / "upstream" / "projection.json"
        proj = json.loads(proj_path.read_text(encoding="utf-8"))
        proj["mode"] = "upstream-portable"
        proj_path.write_text(json.dumps(proj, indent=2), encoding="utf-8")
        failed = {
            c.name: c.detail
            for c in verify_projection.run_checks(root) if not c.ok
        }
        assert any(
            "upstream-portable" in name and "zero content transforms" in name
            for name in failed
        )
        # …and passes when the content transforms are gone.
        proj["transforms"] = [
            t for t in proj["transforms"] if t["id"] not in verify_projection.CONTENT_TRANSFORM_IDS
        ]
        proj_path.write_text(json.dumps(proj, indent=2), encoding="utf-8")
        # Rebuild the skill tree as an identity projection (the hypothetical
        # upstream-portable case: upstream ships a conformant skill as-is).
        for name in projection.EXPECTED_FILES:
            if name == "README.md":
                continue
            target = root / "skills" / "cua-driver" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(root / "upstream" / "source" / "cua-driver" / name, target)
        checks = verify_projection.run_checks(root)
        portable_ok = [
            c for c in checks if "upstream-portable" in c.name and "zero content" in c.name
        ]
        assert portable_ok and portable_ok[0].ok
