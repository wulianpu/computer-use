"""Tests for scripts/verify_upstream.py against synthetic repos and the real repo."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import verify_upstream  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

NOTICES = """# Third-Party Notices
trycua/cua 0.24.0 cua-driver-rs-v0.24.0 4b3396d9fe4bd3cf723b0eb8db83c18a8764b520
libs/cua-driver/rust/Skills/cua-driver MIT
"""

LICENSE = "MIT License\n\nPermission is hereby granted, free of charge.\n"


def make_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    source = root / "upstream" / "source" / "cua-driver"
    source.mkdir(parents=True)
    hashes = {}
    for name in verify_upstream.EXPECTED_FILES:
        data = f"# {name}\nupstream content\n".encode()
        (source / name).write_bytes(data)
        hashes[name] = hashlib.sha256(data).hexdigest()
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

    def test_tampered_source_file_fails(self, tmp_path):
        root = make_repo(tmp_path)
        target = root / "upstream" / "source" / "cua-driver" / "SKILL.md"
        target.write_bytes(target.read_bytes() + b"manual edit\n")
        checks = verify_upstream.run_checks(root)
        assert any("sha256 matches lock: SKILL.md" in f for f in failures(checks))

    def test_unexpected_source_file_fails(self, tmp_path):
        root = make_repo(tmp_path)
        (root / "upstream" / "source" / "cua-driver" / "HISTORY.md").write_text(
            "extra", encoding="utf-8"
        )
        checks = verify_upstream.run_checks(root)
        assert any("no unexpected source files" in f for f in failures(checks))

    def test_missing_source_file_fails(self, tmp_path):
        root = make_repo(tmp_path)
        (root / "upstream" / "source" / "cua-driver" / "BROWSER.md").unlink()
        checks = verify_upstream.run_checks(root)
        assert any("source file exists: BROWSER.md" in f for f in failures(checks))

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
        source = ROOT / "upstream" / "source" / "cua-driver"
        if not (source / "SKILL.md").is_file():
            pytest.skip("upstream source not synced yet — run scripts/sync_cua.py first")
        checks = verify_upstream.run_checks(ROOT)
        assert failures(checks) == []
