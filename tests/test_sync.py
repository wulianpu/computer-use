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
    def files(self):
        return [(name, "file") for name in sync_cua.EXPECTED_FILES]

    def test_exact_set_passes(self):
        assert sync_cua.check_shape(self.files()) == set(sync_cua.EXPECTED_FILES)

    def test_extra_file_fails_closed(self):
        with pytest.raises(sync_cua.SyncError, match="shape changed"):
            sync_cua.check_shape(self.files() + [("HISTORY.md", "file")])

    def test_missing_file_fails_closed(self):
        files = [(n, "file") for n in sync_cua.EXPECTED_FILES if n != "BROWSER.md"]
        with pytest.raises(sync_cua.SyncError, match="shape changed"):
            sync_cua.check_shape(files)

    def test_new_upstream_directory_fails_closed(self):
        entries = self.files() + [("references", "dir")]
        with pytest.raises(sync_cua.SyncError, match=r"directories=\['references'\]"):
            sync_cua.check_shape(entries)

    def test_file_becomes_directory_fails_closed(self):
        entries = [(n, "dir" if n == "SKILL.md" else "file") for n in sync_cua.EXPECTED_FILES]
        with pytest.raises(sync_cua.SyncError, match="shape changed"):
            sync_cua.check_shape(entries)


class TestParseLsTree:
    def test_parses_non_recursive_children_with_kinds(self):
        out = (
            "100644 blob aa\tlibs/cua-driver/rust/Skills/cua-driver/SKILL.md\n"
            "100644 blob bb\tlibs/cua-driver/rust/Skills/cua-driver/WINDOWS.md\n"
            "040000 tree cc\tlibs/cua-driver/rust/Skills/cua-driver/references\n"
        )
        entries = sync_cua._parse_ls_tree(out, "libs/cua-driver/rust/Skills/cua-driver")
        assert ("SKILL.md", "file") in entries
        assert ("references", "dir") in entries
        assert len(entries) == 3

    def test_ignores_the_directory_row_itself(self):
        out = "040000 tree dd\tlibs/cua-driver/rust/Skills/cua-driver\n"
        assert sync_cua._parse_ls_tree(out, "libs/cua-driver/rust/Skills/cua-driver") == []


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


# --------------------------------------------------- full run with fake net


class FakeUpstream:
    """Deterministic stand-in for the network layer."""

    def __init__(self, repository, token=None):
        pass

    def resolve_tag(self, tag):
        return "f" * 40

    def list_dir(self, commit, path):
        return [(name, "file") for name in sync_cua.EXPECTED_FILES]

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
    return root


class TestMainEndToEnd:
    def test_full_sync_writes_source_cache_and_lock_only(self, tmp_path, monkeypatch):
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
        expected_hash = sync_cua.sha256_hex(
            f"content of {sync_cua.DEFAULT_SOURCE_PATH}/SKILL.md\n".encode()
        )
        assert lock["files"]["SKILL.md"]["sha256"] == expected_hash

        source = root / "upstream" / "source" / "cua-driver"
        files = sorted(p.name for p in source.iterdir())
        assert files == sorted(sync_cua.EXPECTED_FILES)
        assert (source / "SKILL.md").read_bytes() == (
            f"content of {sync_cua.DEFAULT_SOURCE_PATH}/SKILL.md\n".encode()
        )

        # Scope (§22): sync owns downloading and pinning only — it must NOT
        # touch compatibility receipts or any skill file.
        compat = json.loads((root / "upstream" / "compatibility.json").read_text(encoding="utf-8"))
        assert compat["candidate"] == {"version": "0.23.0", "tag": "old"}
        assert not (root / "skills").exists()

        notices = (root / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        assert "0.24.0" in notices and "f" * 40 in notices
        assert (root / "licenses" / "CUA-LICENSE.md").is_file()

    def test_removes_stale_source_files(self, tmp_path, monkeypatch):
        root = _make_repo(tmp_path)
        source = root / "upstream" / "source" / "cua-driver"
        source.mkdir(parents=True)
        (source / "OLD.md").write_text("stale", encoding="utf-8")
        monkeypatch.setattr(sync_cua, "Upstream", FakeUpstream)
        assert sync_cua.main(["--tag", "cua-driver-rs-v0.24.0", "--root", str(root)]) == 0
        assert not (source / "OLD.md").exists()

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
