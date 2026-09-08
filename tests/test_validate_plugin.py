"""Tests for scripts/validate_plugin.py: official-schema + project-policy model.

The official pinned schemas under schemas/agent-plugins/1.0.0/ are the
authority for field sets; these tests prove the validator rejects spec
violations (instead of trusting our own field lists), enforces the mcp.json
deterministic template, and fully checks projected-Skill frontmatter
conformance (design doc v2 §34, §44).
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import validate_plugin  # noqa: E402


def failures(checks) -> dict[str, str]:
    return {c.name: c.detail for c in checks if not c.ok}


def make_repo(tmp_path: Path) -> Path:
    """A structural copy of the real plugin (projected skill included)."""
    root = tmp_path / "repo"
    root.mkdir()
    for rel in ("plugin.json", "mcp.json"):
        shutil.copy(ROOT / rel, root / rel)
    shutil.copytree(ROOT / "schemas", root / "schemas")
    shutil.copytree(ROOT / "skills", root / "skills")
    return root


def rewrite(root: Path, rel: str, mutate) -> None:
    doc = json.loads((root / rel).read_text(encoding="utf-8"))
    mutate(doc)
    (root / rel).write_text(json.dumps(doc, indent=2), encoding="utf-8")


class TestOfficialSchemaIsTheAuthority:
    def test_clean_repo_passes(self, tmp_path):
        assert failures(validate_plugin.run_checks(make_repo(tmp_path))) == {}

    def test_rejects_unknown_manifest_field_links(self, tmp_path):
        # 'links' is NOT a 1.0.0 field; the pinned schema catches it.
        root = make_repo(tmp_path)
        rewrite(root, "plugin.json", lambda d: d.update({"links": {"x": "https://y"}}))
        assert any(
            "official Agent Plugins schema" in name
            for name in failures(validate_plugin.run_checks(root))
        )

    def test_accepts_extensions_field(self, tmp_path):
        # 'extensions' IS a 1.0.0 field.
        root = make_repo(tmp_path)
        rewrite(root, "plugin.json", lambda d: d.update({"extensions": {"x": {"y": 1}}}))
        schema_failures = [
            n
            for n in failures(validate_plugin.run_checks(root))
            if "official Agent Plugins schema" in n
        ]
        assert schema_failures == []

    def test_rejects_unknown_top_level_mcp_key(self, tmp_path):
        root = make_repo(tmp_path)
        rewrite(root, "mcp.json", lambda d: d.update({"servers": {}}))
        assert any(
            "mcp.json conforms" in name for name in failures(validate_plugin.run_checks(root))
        )

    def test_rejects_unknown_server_entry_key(self, tmp_path):
        root = make_repo(tmp_path)

        def mutate(doc):
            doc["mcpServers"]["cua-driver"]["stdio"] = True

        rewrite(root, "mcp.json", mutate)
        assert any(
            "mcp.json conforms" in name for name in failures(validate_plugin.run_checks(root))
        )


class TestMcpJsonIsDeterministic:
    def test_template_equality_enforced(self, tmp_path):
        root = make_repo(tmp_path)
        rewrite(
            root, "mcp.json", lambda d: d["mcpServers"]["cua-driver"].update({"env": {"X": "1"}})
        )
        result = failures(validate_plugin.run_checks(root))
        assert any("deterministic generator template" in name for name in result)

    def test_rejects_wrapper_command(self, tmp_path):
        root = make_repo(tmp_path)

        def mutate(doc):
            entry = doc["mcpServers"]["cua-driver"]
            entry["command"] = "bash"
            entry["args"] = ["-c", "cua-driver mcp"]

        rewrite(root, "mcp.json", mutate)
        result = failures(validate_plugin.run_checks(root))
        assert any("command directly invokes cua-driver" in name for name in result)
        assert any("no wrapper/interpreter command" in name for name in result)


class TestProjectedSkillConformance:
    def test_skill_frontmatter_is_agent_skills_conformant(self):
        # The real (projected) skill passes every structural rule.
        result = failures(validate_plugin.run_checks(ROOT))
        assert not any(
            "skill name" in n
            or "frontmatter" in n
            or "description" in n
            or "compatibility" in n
            or "metadata" in n
            for n in result
        )

    def test_rejects_non_conformant_skill_name(self, tmp_path):
        root = make_repo(tmp_path)
        skill = root / "skills" / "cua-driver" / "SKILL.md"
        text = skill.read_text(encoding="utf-8").replace(
            'name: "cua-driver"', 'name: "cua--driver-"', 1
        )
        skill.write_text(text, encoding="utf-8")
        result = failures(validate_plugin.run_checks(root))
        assert any("Agent-Skills-conformant" in name for name in result)
        assert any("name matches directory" in name for name in result)

    def test_rejects_manual_skill_edit_without_generated_notice(self, tmp_path):
        root = make_repo(tmp_path)
        skill = root / "skills" / "cua-driver" / "SKILL.md"
        text = skill.read_text(encoding="utf-8").replace("GENERATED FILE", "EDITED FILE", 1)
        skill.write_text(text, encoding="utf-8")
        assert any(
            "marked as generated" in name for name in failures(validate_plugin.run_checks(root))
        )

    def test_rejects_legacy_references_model(self, tmp_path):
        root = make_repo(tmp_path)
        legacy = root / "skills" / "cua-driver" / "references" / "upstream"
        legacy.mkdir(parents=True)
        (legacy / "SKILL.md").write_text("old model", encoding="utf-8")
        result = failures(validate_plugin.run_checks(root))
        assert any("legacy references" in name for name in result)
        assert any("projected skill file set" in name for name in result)

    def test_rejects_missing_companion(self, tmp_path):
        root = make_repo(tmp_path)
        (root / "skills" / "cua-driver" / "BROWSER.md").unlink()
        assert any(
            "projected skill file set" in name
            for name in failures(validate_plugin.run_checks(root))
        )


class TestProjectPolicy:
    def test_rejects_forbidden_manifest_key(self, tmp_path):
        root = make_repo(tmp_path)
        rewrite(root, "plugin.json", lambda d: d.update({"cuaVersion": "0.24.0"}))
        assert any(
            "forbidden manifest keys" in name for name in failures(validate_plugin.run_checks(root))
        )

    def test_rejects_second_skill_directory(self, tmp_path):
        root = make_repo(tmp_path)
        (root / "skills" / "other-skill").mkdir()
        assert any(
            "skills contains only" in name for name in failures(validate_plugin.run_checks(root))
        )


class TestFrontmatterParser:
    def test_parses_nested_metadata_strings(self):
        text = (
            "---\n"
            "name: cua-driver\n"
            "description: some description.\n"
            "metadata:\n"
            '  upstream: "trycua/cua"\n'
            '  upstream-version: "0.24.0"\n'
            "---\nbody\n"
        )
        fm, error = validate_plugin._parse_frontmatter(text)
        assert error is None
        assert fm["name"] == "cua-driver"
        assert fm["metadata"]["upstream-version"] == "0.24.0"

    def test_rejects_unterminated_frontmatter(self):
        fm, error = validate_plugin._parse_frontmatter("---\nname: x\n")
        assert fm is None and error == "unterminated frontmatter"
