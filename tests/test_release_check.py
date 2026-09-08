"""Tests for scripts/release_check.py (machine-provable release gate).

Every scratch repository configures a REPO-LOCAL git identity so the tests
are hermetic: they never depend on the developer machine's (or a clean CI
runner's) global git user configuration.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import release_check  # noqa: E402


def git(root: Path, *args: str) -> str:
    env = dict(os.environ)
    env["GIT_CONFIG_GLOBAL"] = os.devnull  # ignore any global identity
    env["GIT_CONFIG_SYSTEM"] = os.devnull  # ignore any system identity
    proc = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, env=env, timeout=60
    )
    if proc.returncode != 0:
        raise AssertionError(f"git {args} failed: {proc.stderr}")
    return proc.stdout.strip()


def make_git_repo(tmp_path: Path) -> Path:
    """A scratch repo with the full production/evidence surface and one commit."""
    root = tmp_path / "repo"
    root.mkdir()
    for rel in ("plugin.json", "mcp.json", "CHANGELOG.md", "THIRD_PARTY_NOTICES.md", "LICENSE"):
        shutil.copy(ROOT / rel, root / rel)
    for d in ("skills", "upstream", "licenses", "schemas"):
        shutil.copytree(ROOT / d, root / d)
    # the qualification harness (its digest is bound into receipts)
    (root / "scripts").mkdir()
    for f in ("mcp_client.py", "mcp_probe.py", "e2e_calculator.py"):
        shutil.copy(ROOT / "scripts" / f, root / "scripts" / f)
    (root / "tests" / "contract").mkdir(parents=True)
    shutil.copy(
        ROOT / "tests" / "contract" / "required-tools.json",
        root / "tests" / "contract" / "required-tools.json",
    )
    git(root, "init", "-q", "-b", "main")
    # Repo-local identity: commits AND annotated tags resolve without any
    # global/system git config (exactly what a clean runner lacks).
    git(root, "config", "user.name", "computer-use-test")
    git(root, "config", "user.email", "computer-use-test@example.invalid")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    return root


def requalify_receipt(root: Path) -> None:
    """Point the receipt at THIS scratch repo's digests and HEAD (simulating
    a fresh, honest qualification of the copied surface)."""
    compat_path = root / "upstream" / "compatibility.json"
    compat = json.loads(compat_path.read_text(encoding="utf-8"))
    entry = compat["verified"][0]
    proj = json.loads((root / "upstream" / "projection.json").read_text(encoding="utf-8"))
    lock = json.loads((root / "upstream" / "cua.lock.json").read_text(encoding="utf-8"))
    entry["version"] = lock["version"]
    entry["upstreamCommit"] = lock["commit"]
    entry["skillSource"] = lock["skillSource"]
    entry["projectionMode"] = proj["mode"]
    entry["projectionDigest"] = proj["projectionDigest"]
    entry["pluginSurfaceDigest"] = proj["pluginSurfaceDigest"]
    sys.path.insert(0, str(SCRIPTS))
    import verify_projection as _vp

    entry["qualificationHarnessDigest"] = _vp.qualification_harness_digest(root)
    entry["testedPluginCommit"] = git(root, "rev-parse", "HEAD")
    compat_path.write_text(
        json.dumps(compat, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def failures(checks):
    return [c.name for c in checks if not c.ok]


class TestReleaseCheckPositive:
    def test_fully_valid_release_is_ready(self, tmp_path):
        root = make_git_repo(tmp_path)
        requalify_receipt(root)
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "qualification receipt")
        git(root, "tag", "-a", "v0.1.0", "-m", "release")
        checks = release_check.run_checks(root, "v0.1.0")
        assert failures(checks) == []

    def test_docs_only_commits_after_qualification_stay_ready(self, tmp_path):
        root = make_git_repo(tmp_path)
        requalify_receipt(root)
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "qualification receipt")
        # Docs-only commits AFTER qualification must not invalidate anything.
        (root / "NOTES.md").write_text("docs only\n", encoding="utf-8")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "docs-only polish")
        git(root, "tag", "-a", "v0.1.0", "-m", "release")
        checks = release_check.run_checks(root, "v0.1.0")
        assert failures(checks) == []
        for scope in ("production surface unchanged", "qualification harness unchanged"):
            chain = [c for c in checks if scope in c.name]
            assert chain and chain[0].ok, scope


class TestReleaseCheckNegative:
    def test_stale_receipt_is_not_ready(self, tmp_path):
        """A copied (un-requalified) receipt binds the real repo's digests, so a
        scratch repo must NOT be release-ready."""
        root = make_git_repo(tmp_path)
        git(root, "tag", "-a", "v0.1.0", "-m", "release")
        checks = release_check.run_checks(root, "v0.1.0")
        assert failures(checks)

    def test_version_mismatch_blocks_release(self, tmp_path):
        root = make_git_repo(tmp_path)
        requalify_receipt(root)
        manifest = root / "plugin.json"
        doc = json.loads(manifest.read_text(encoding="utf-8"))
        doc["version"] = "9.9.9"
        manifest.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "bump")
        git(root, "tag", "-a", "v0.1.0", "-m", "release")
        checks = release_check.run_checks(root, "v0.1.0")
        assert any("plugin.json version" in name for name in failures(checks))

    def test_production_change_after_qualification_blocks_release(self, tmp_path):
        """Even with digests aligned, a production byte changed after the
        receipt's testedPluginCommit must block the release (docs-only is fine)."""
        root = make_git_repo(tmp_path)
        requalify_receipt(root)
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "qualification receipt")
        skill = root / "skills" / "cua-driver" / "SKILL.md"
        skill.write_text(skill.read_text(encoding="utf-8") + "\nedit\n", encoding="utf-8")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "prod change")
        git(root, "tag", "-a", "v0.1.0", "-m", "release")
        checks = release_check.run_checks(root, "v0.1.0")
        assert any("production surface unchanged" in name for name in failures(checks))

    def test_unrelated_history_with_identical_bytes_blocks_release(self, tmp_path):
        """Diff-emptiness is not lineage: an orphan branch carrying
        byte-identical production + harness files, with a testedPluginCommit
        that exists in the repository but is NOT an ancestor of the release
        commit, must fail the ancestry proof."""
        root = make_git_repo(tmp_path)
        requalify_receipt(root)
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "qualification receipt")
        # receipt keeps testedPluginCommit = current HEAD (exists in repo)
        # ...then the release is cut from an UNRELATED orphan history that
        # happens to carry identical production/harness bytes.
        git(root, "checkout", "-q", "--orphan", "release-orphan")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "orphan release commit")
        git(root, "tag", "-a", "v0.1.0", "-m", "release")
        checks = release_check.run_checks(root, "v0.1.0")
        failed_names = failures(checks)
        assert any("is an ancestor of the release commit" in name for name in failed_names), (
            failed_names
        )

    def test_harness_change_after_qualification_blocks_release(self, tmp_path):
        """A qualification-harness edit after the tested commit blocks the
        release even if someone hand-re-signs the digests in the receipt."""
        root = make_git_repo(tmp_path)
        requalify_receipt(root)
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "qualification receipt")
        harness = root / "scripts" / "mcp_client.py"
        harness.write_text(harness.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
        # hand-re-sign the harness digest so digest equality alone would pass
        import sys as _sys

        _sys.path.insert(0, str(SCRIPTS))
        import verify_projection as _vp

        compat_path = root / "upstream" / "compatibility.json"
        compat = json.loads(compat_path.read_text(encoding="utf-8"))
        compat["verified"][0]["qualificationHarnessDigest"] = _vp.qualification_harness_digest(root)
        compat_path.write_text(json.dumps(compat, indent=2) + "\n", encoding="utf-8", newline="\n")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "harness edit + hand-re-signed digest")
        git(root, "tag", "-a", "v0.1.0", "-m", "release")
        checks = release_check.run_checks(root, "v0.1.0")
        assert any("qualification harness unchanged" in name for name in failures(checks))
