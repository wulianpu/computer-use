"""Unit tests for scripts/sync_cua.py (pure logic + a fully mocked sync run)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import sync_cua  # noqa: E402


# ------------------------------------------------------------ pure helpers


class TestVersionFromTag:
    def test_extracts_version(self):
        assert sync_cua.version_from_tag("cua-driver-rs-v0.24.0") == "0.24.0"
        assert sync_cua.version_from_tag("v1.2.3") == "1.2.3"

    def test_rejects_non_version_tag(self):
        with pytest.raises(sync_cua.SyncError):
            sync_cua.version_from_tag("latest")


class TestCheckShape:
    def test_exact_set_passes(self):
        assert sync_cua.check_shape(sync_cua.EXPECTED_FILES) == set(sync_cua.EXPECTED_FILES)

    def test_extra_file_fails_closed(self):
        with pytest.raises(sync_cua.SyncError, match="shape changed"):
            sync_cua.check_shape([*sync_cua.EXPECTED_FILES, "HISTORY.md"])

    def test_missing_file_fails_closed(self):
        files = [f for f in sync_cua.EXPECTED_FILES if f != "BROWSER.md"]
        with pytest.raises(sync_cua.SyncError, match="shape changed"):
            sync_cua.check_shape(files)


class TestLock:
    def test_build_lock_covers_all_expected_files_in_order(self):
        hashes = {name: "0" * 64 for name in sync_cua.EXPECTED_FILES}
        lock = sync_cua.build_lock("trycua/cua", "0.24.0", "t", "c", "src", hashes)
        assert list(lock["files"]) == list(sync_cua.EXPECTED_FILES)
        assert lock["files"]["SKILL.md"] == {"sha256": "0" * 64}
        assert lock["repository"] == "trycua/cua"

    def test_sha256_hex_is_stable(self):
        assert sync_cua.sha256_hex(b"abc") == (
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        )


THIN_SKILL_TEMPLATE = """---
name: cua-driver
description: some description here.
license: MIT
compatibility: Requires a compatible cua-driver installation and an Agent Plugin host with MCP stdio support. Upstream guidance is qualified against Cua Driver 0.23.0.
metadata:
  upstream: "trycua/cua"
  upstream-version: "0.23.0"
  projection: "computer-use"
---

# body
"""


class TestThinSkillUpdate:
    def test_updates_version_lines(self, tmp_path):
        skill = tmp_path / "SKILL.md"
        skill.write_text(THIN_SKILL_TEMPLATE, encoding="utf-8")
        sync_cua.update_thin_skill(skill, "0.24.0")
        text = skill.read_text(encoding="utf-8")
        assert 'upstream-version: "0.24.0"' in text
        assert "qualified against Cua Driver 0.24.0." in text
        assert "0.23.0" not in text

    def test_fails_when_frontmatter_drifts(self, tmp_path):
        skill = tmp_path / "SKILL.md"
        skill.write_text("---\nname: cua-driver\n---\n", encoding="utf-8")
        with pytest.raises(sync_cua.SyncError, match="frontmatter"):
            sync_cua.update_thin_skill(skill, "0.24.0")


class TestCompatibilityUpdate:
    def test_drops_verified_entries_from_other_versions(self, tmp_path):
        path = tmp_path / "compatibility.json"
        path.write_text(json.dumps({
            "candidate": {"version": "0.23.0", "tag": "old"},
            "verified": [
                {"version": "0.23.0", "platform": "windows"},
                {"version": "0.22.0", "platform": "linux"},
            ],
            "unsupported": [],
        }), encoding="utf-8")
        sync_cua.update_compatibility(path, {"version": "0.24.0", "tag": "new"})
        compat = json.loads(path.read_text(encoding="utf-8"))
        assert compat["candidate"] == {"version": "0.24.0", "tag": "new"}
        assert [e["version"] for e in compat["verified"]] == []


# --------------------------------------------------- full run with fake net


class FakeUpstream:
    """Deterministic stand-in for the network layer."""

    def __init__(self, repository, token=None):
        pass

    def resolve_tag(self, tag):
        return "f" * 40

    def list_dir(self, commit, path):
        return list(sync_cua.EXPECTED_FILES)

    def fetch_file(self, commit, path):
        return f"content of {path}\n".encode()

    def fetch_license(self, commit):
        return "LICENSE.md", b"MIT License\n\nPermission is hereby granted, free of charge...\n"


def _make_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "upstream").mkdir(parents=True)
    (root / "upstream" / "compatibility.json").write_text(
        json.dumps({"candidate": {"version": "0.23.0", "tag": "old"},
                    "verified": [], "unsupported": []}),
        encoding="utf-8",
    )
    skill_dir = root / "skills" / "cua-driver" / "references" / "upstream"
    skill_dir.mkdir(parents=True)
    (root / "skills" / "cua-driver" / "SKILL.md").write_text(
        THIN_SKILL_TEMPLATE, encoding="utf-8"
    )
    return root


class TestMainEndToEnd:
    def test_full_sync_writes_all_artifacts(self, tmp_path, monkeypatch):
        root = _make_repo(tmp_path)
        monkeypatch.setattr(sync_cua, "Upstream", FakeUpstream)
        rc = sync_cua.main([
            "--tag", "cua-driver-rs-v0.24.0",
            "--root", str(root),
        ])
        assert rc == 0

        lock = json.loads((root / "upstream" / "cua.lock.json").read_text(encoding="utf-8"))
        assert lock["version"] == "0.24.0"
        assert lock["commit"] == "f" * 40
        assert lock["skillSource"] == sync_cua.DEFAULT_SOURCE_PATH
        expected_hash = sync_cua.sha256_hex(f"content of {sync_cua.DEFAULT_SOURCE_PATH}/SKILL.md\n".encode())
        assert lock["files"]["SKILL.md"]["sha256"] == expected_hash

        mirror = root / "skills" / "cua-driver" / "references" / "upstream"
        mirrored = sorted(p.name for p in mirror.iterdir())
        assert mirrored == sorted(sync_cua.EXPECTED_FILES)
        assert (mirror / "SKILL.md").read_bytes() == f"content of {sync_cua.DEFAULT_SOURCE_PATH}/SKILL.md\n".encode()

        compat = json.loads((root / "upstream" / "compatibility.json").read_text(encoding="utf-8"))
        assert compat["candidate"] == {"version": "0.24.0", "tag": "cua-driver-rs-v0.24.0"}

        skill = (root / "skills" / "cua-driver" / "SKILL.md").read_text(encoding="utf-8")
        assert 'upstream-version: "0.24.0"' in skill

        notices = (root / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        assert "0.24.0" in notices and "f" * 40 in notices
        assert (root / "licenses" / "CUA-LICENSE.md").is_file()

    def test_removes_stale_mirror_files(self, tmp_path, monkeypatch):
        root = _make_repo(tmp_path)
        stale = root / "skills" / "cua-driver" / "references" / "upstream" / "OLD.md"
        stale.write_text("stale", encoding="utf-8")
        monkeypatch.setattr(sync_cua, "Upstream", FakeUpstream)
        assert sync_cua.main(["--tag", "cua-driver-rs-v0.24.0", "--root", str(root)]) == 0
        assert not stale.exists()

    def test_source_path_change_requires_flag(self, tmp_path, monkeypatch):
        root = _make_repo(tmp_path)
        monkeypatch.setattr(sync_cua, "Upstream", FakeUpstream)
        # First sync establishes the lock with the default source path.
        assert sync_cua.main(["--tag", "cua-driver-rs-v0.24.0", "--root", str(root)]) == 0
        # A second sync pointing at a different path must fail closed.
        with pytest.raises(sync_cua.SyncError, match="Manual review required"):
            sync_cua.main([
                "--tag", "cua-driver-rs-v0.24.0",
                "--root", str(root),
                "--source-path", "AgentPlugin/skills/cua-driver",
            ])
        # …and succeed only with the explicit acknowledgement.
        assert sync_cua.main([
            "--tag", "cua-driver-rs-v0.24.0",
            "--root", str(root),
            "--source-path", "AgentPlugin/skills/cua-driver",
            "--allow-source-path-change",
        ]) == 0
        lock = json.loads((root / "upstream" / "cua.lock.json").read_text(encoding="utf-8"))
        assert lock["skillSource"] == "AgentPlugin/skills/cua-driver"

    def test_commit_mismatch_fails(self, tmp_path, monkeypatch):
        root = _make_repo(tmp_path)
        monkeypatch.setattr(sync_cua, "Upstream", FakeUpstream)
        with pytest.raises(sync_cua.SyncError, match="does not match"):
            sync_cua.main([
                "--tag", "cua-driver-rs-v0.24.0",
                "--root", str(root),
                "--commit", "deadbeef",
            ])
