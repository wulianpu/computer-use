"""Tests for scripts/verify_upstream.py against synthetic repos and the real repo."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import verify_upstream  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

THIN_SKILL = """---
name: cua-driver
description: thin skill description.
license: MIT
compatibility: Requires a compatible cua-driver installation and an Agent Plugin host with MCP stdio support. Upstream guidance is qualified against Cua Driver 0.24.0.
metadata:
  upstream: "trycua/cua"
  upstream-version: "0.24.0"
  projection: "computer-use"
---
body
"""

NOTICES = """# Third-Party Notices
trycua/cua 0.24.0 cua-driver-rs-v0.24.0 4b3396d9fe4bd3cf723b0eb8db83c18a8764b520
libs/cua-driver/rust/Skills/cua-driver MIT
"""

LICENSE = "MIT License\n\nPermission is hereby granted, free of charge.\n"


def make_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    mirror = root / "skills" / "cua-driver" / "references" / "upstream"
    mirror.mkdir(parents=True)
    hashes = {}
    for name in verify_upstream.EXPECTED_FILES:
        data = f"# {name}\nupstream content\n".encode()
        (mirror / name).write_bytes(data)
        import hashlib

        hashes[name] = hashlib.sha256(data).hexdigest()
    (root / "skills" / "cua-driver" / "SKILL.md").write_text(THIN_SKILL, encoding="utf-8")
    (root / "upstream").mkdir()
    (root / "upstream" / "cua.lock.json").write_text(json.dumps({
        "repository": "trycua/cua",
        "version": "0.24.0",
        "tag": "cua-driver-rs-v0.24.0",
        "commit": "4b3396d9fe4bd3cf723b0eb8db83c18a8764b520",
        "skillSource": "libs/cua-driver/rust/Skills/cua-driver",
        "files": {name: {"sha256": digest} for name, digest in hashes.items()},
    }, indent=2), encoding="utf-8")
    (root / "THIRD_PARTY_NOTICES.md").write_text(NOTICES, encoding="utf-8")
    (root / "licenses").mkdir()
    (root / "licenses" / "CUA-LICENSE.md").write_text(LICENSE, encoding="utf-8")
    return root


def failures(checks) -> list[str]:
    return [f"{c.name}: {c.detail}" for c in checks if not c.ok]


class TestSyntheticRepo:
    def test_clean_repo_passes_everything(self, tmp_path):
        checks = verify_upstream.run_checks(make_repo(tmp_path))
        assert failures(checks) == []

    def test_tampered_mirror_file_fails(self, tmp_path):
        root = make_repo(tmp_path)
        target = root / "skills" / "cua-driver" / "references" / "upstream" / "SKILL.md"
        target.write_bytes(target.read_bytes() + b"manual edit\n")
        checks = verify_upstream.run_checks(root)
        assert any("sha256 matches lock: SKILL.md" in f for f in failures(checks))

    def test_unexpected_mirror_file_fails(self, tmp_path):
        root = make_repo(tmp_path)
        (root / "skills" / "cua-driver" / "references" / "upstream" / "HISTORY.md").write_text(
            "extra", encoding="utf-8"
        )
        checks = verify_upstream.run_checks(root)
        assert any("no unexpected mirrored files" in f for f in failures(checks))

    def test_missing_mirror_file_fails(self, tmp_path):
        root = make_repo(tmp_path)
        (root / "skills" / "cua-driver" / "references" / "upstream" / "BROWSER.md").unlink()
        checks = verify_upstream.run_checks(root)
        assert any("mirror file exists: BROWSER.md" in f for f in failures(checks))

    def test_version_mismatch_between_skill_and_lock_fails(self, tmp_path):
        root = make_repo(tmp_path)
        skill = root / "skills" / "cua-driver" / "SKILL.md"
        skill.write_text(
            skill.read_text(encoding="utf-8").replace("0.24.0", "0.23.0"),
            encoding="utf-8",
        )
        checks = verify_upstream.run_checks(root)
        assert any("thin Skill version matches lock" in f for f in failures(checks))

    def test_missing_license_fails(self, tmp_path):
        root = make_repo(tmp_path)
        (root / "licenses" / "CUA-LICENSE.md").unlink()
        checks = verify_upstream.run_checks(root)
        assert any("upstream license exists" in f for f in failures(checks))

    def test_notices_drift_fails(self, tmp_path):
        root = make_repo(tmp_path)
        (root / "THIRD_PARTY_NOTICES.md").write_text("stale notices: 0.19.2\n", encoding="utf-8")
        checks = verify_upstream.run_checks(root)
        assert any("third-party notice matches lock" in f for f in failures(checks))


class TestRealRepo:
    def test_real_repo_upstream_verification_passes(self):
        mirror = ROOT / "skills" / "cua-driver" / "references" / "upstream"
        if not (mirror / "SKILL.md").is_file():
            pytest.skip("upstream mirror not synced yet — run scripts/sync_cua.py first")
        checks = verify_upstream.run_checks(ROOT)
        assert failures(checks) == []


class TestFrontmatterParser:
    def test_parses_nested_metadata_strings(self):
        fm, error = verify_upstream._parse_frontmatter(THIN_SKILL)
        assert error is None
        assert fm["name"] == "cua-driver"
        assert fm["metadata"]["upstream-version"] == "0.24.0"

    def test_rejects_unterminated_frontmatter(self):
        fm, error = verify_upstream._parse_frontmatter("---\nname: x\n")
        assert fm is None and error == "unterminated frontmatter"
