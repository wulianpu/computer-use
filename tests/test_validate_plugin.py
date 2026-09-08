"""Tests for scripts/validate_plugin.py: official-schema + project-policy model.

The official pinned schemas under schemas/agent-plugins/1.0.0/ are the
authority for field sets; these tests prove the validator actually rejects
spec violations (instead of trusting our own field lists) and that the
project policy layer enforces the mcp.json transport invariants.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import validate_plugin  # noqa: E402


def failures(checks) -> list[str]:
    return [f"{c.name}: {c.detail}" for c in checks if not c.ok]


def make_repo(tmp_path: Path) -> Path:
    """A structural copy of the real plugin, minimal but fully valid."""
    root = tmp_path / "repo"
    root.mkdir()
    for rel in ("plugin.json", "mcp.json"):
        shutil.copy(ROOT / rel, root / rel)
    shutil.copytree(ROOT / "schemas", root / "schemas")
    skill_dir = root / "skills" / "cua-driver" / "references" / "upstream"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("upstream", encoding="utf-8")
    (root / "skills" / "cua-driver" / "SKILL.md").write_text(
        (ROOT / "skills" / "cua-driver" / "SKILL.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return root


def rewrite(root: Path, rel: str, mutate) -> None:
    doc = json.loads((root / rel).read_text(encoding="utf-8"))
    mutate(doc)
    (root / rel).write_text(json.dumps(doc, indent=2), encoding="utf-8")


class TestOfficialSchemaIsTheAuthority:
    def test_clean_repo_passes(self, tmp_path):
        assert failures(validate_plugin.run_checks(make_repo(tmp_path))) == []

    def test_rejects_unknown_manifest_field_links(self, tmp_path):
        # 'links' is NOT a 1.0.0 field (a field our earlier hand-written
        # allow-list wrongly permitted — the schema catches it now).
        root = make_repo(tmp_path)
        rewrite(root, "plugin.json", lambda d: d.update({"links": {"x": "https://y"}}))
        assert any("official Agent Plugins schema" in f for f in
                   failures(validate_plugin.run_checks(root)))

    def test_accepts_extensions_field(self, tmp_path):
        # 'extensions' IS a 1.0.0 field our earlier allow-list wrongly rejected.
        root = make_repo(tmp_path)
        rewrite(root, "plugin.json", lambda d: d.update({"extensions": {"x": {"y": 1}}}))
        schema_failures = [f for f in failures(validate_plugin.run_checks(root))
                           if "official Agent Plugins schema" in f]
        assert schema_failures == []

    def test_rejects_missing_required_manifest_name(self, tmp_path):
        root = make_repo(tmp_path)
        rewrite(root, "plugin.json", lambda d: d.pop("name"))
        assert any("official Agent Plugins schema" in f for f in
                   failures(validate_plugin.run_checks(root)))

    def test_rejects_unknown_top_level_mcp_key(self, tmp_path):
        root = make_repo(tmp_path)
        rewrite(root, "mcp.json", lambda d: d.update({"servers": {}}))
        assert any("mcp.json conforms" in f for f in failures(validate_plugin.run_checks(root)))

    def test_rejects_unknown_server_entry_key(self, tmp_path):
        root = make_repo(tmp_path)
        def mutate(doc):
            doc["mcpServers"]["cua-driver"]["stdio"] = True
        rewrite(root, "mcp.json", mutate)
        assert any("mcp.json conforms" in f for f in failures(validate_plugin.run_checks(root)))


class TestProjectPolicy:
    def test_rejects_wrapper_command(self, tmp_path):
        root = make_repo(tmp_path)
        def mutate(doc):
            entry = doc["mcpServers"]["cua-driver"]
            entry["command"] = "bash"
            entry["args"] = ["-c", "cua-driver mcp"]
        rewrite(root, "mcp.json", mutate)
        result = failures(validate_plugin.run_checks(root))
        assert any("command directly invokes cua-driver" in f for f in result)
        assert any("no wrapper/interpreter command" in f for f in result)

    def test_rejects_bundled_runtime_path(self, tmp_path):
        root = make_repo(tmp_path)
        rewrite(root, "mcp.json",
                lambda d: d["mcpServers"]["cua-driver"].update({"command": "./bin/cua-driver"}))
        result = failures(validate_plugin.run_checks(root))
        assert any("not a local path" in f for f in result)
        assert any("no bundled runtime" in f for f in result)

    def test_rejects_forbidden_manifest_key(self, tmp_path):
        root = make_repo(tmp_path)
        rewrite(root, "plugin.json", lambda d: d.update({"cuaVersion": "0.24.0"}))
        # The official schema rejects the unknown key; project policy also
        # forbids it explicitly for future schema revisions.
        result = failures(validate_plugin.run_checks(root))
        assert any("forbidden manifest keys" in f for f in result)

    def test_rejects_second_skill_directory(self, tmp_path):
        root = make_repo(tmp_path)
        (root / "skills" / "other-skill").mkdir()
        result = failures(validate_plugin.run_checks(root))
        assert any("skills contains only" in f for f in result)
