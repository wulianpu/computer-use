"""Tests for scripts/release_check.py (machine-provable release gate)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import release_check  # noqa: E402


def make_git_repo(tmp_path: Path) -> Path:
    """A scratch repo with the full production/evidence surface and one commit."""
    root = tmp_path / "repo"
    root.mkdir()
    for rel in ("plugin.json", "mcp.json", "CHANGELOG.md", "THIRD_PARTY_NOTICES.md", "LICENSE"):
        shutil.copy(ROOT / rel, root / rel)
    for d in ("skills", "upstream", "licenses", "schemas"):
        shutil.copytree(ROOT / d, root / d)
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "init",
        ],
        check=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "PATH": __import__("os").environ["PATH"],
        },
    )
    subprocess.run(["git", "-C", str(root), "tag", "-a", "v0.1.0", "-m", "t"], check=True)
    return root


def failures(checks):
    return [c.name for c in checks if not c.ok]


class TestReleaseCheck:
    def test_full_surface_repo_with_stale_receipt_is_not_ready(self, tmp_path):
        """The copied receipt still binds the real repo's digests/commit, so a
        scratch repo must NOT be release-ready (tag HEAD/commit differ etc.)."""
        root = make_git_repo(tmp_path)
        checks = release_check.run_checks(root, "v0.1.0")
        assert failures(checks)  # not ready without a matching, current receipt

    def test_version_mismatch_blocks_release(self, tmp_path):
        root = make_git_repo(tmp_path)
        manifest = root / "plugin.json"
        doc = json.loads(manifest.read_text(encoding="utf-8"))
        doc["version"] = "9.9.9"
        manifest.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        checks = release_check.run_checks(root, "v0.1.0")
        assert any("plugin.json version" in name for name in failures(checks))

    def test_production_change_after_qualification_blocks_release(self, tmp_path):
        """Even with digests aligned, a production byte changed after the
        receipt's pluginCommit must block the release (docs-only is fine)."""
        root = make_git_repo(tmp_path)
        compat_path = root / "upstream" / "compatibility.json"
        compat = json.loads(compat_path.read_text(encoding="utf-8"))
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        entry = compat["verified"][0]
        entry["pluginCommit"] = head  # pretend we qualified THIS commit…
        compat_path.write_text(json.dumps(compat, indent=2), encoding="utf-8")
        # …then change production and commit it
        skill = root / "skills" / "cua-driver" / "SKILL.md"
        skill.write_text(skill.read_text(encoding="utf-8") + "\nedit\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t",
                "commit",
                "-q",
                "-m",
                "prod change",
            ],
            check=True,
            env={
                "GIT_AUTHOR_NAME": "t",
                "GIT_AUTHOR_EMAIL": "t@t",
                "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@t",
                "PATH": __import__("os").environ["PATH"],
            },
        )
        checks = release_check.run_checks(root, "v0.1.0")
        assert any("production unchanged" in name for name in failures(checks))
