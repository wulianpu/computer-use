#!/usr/bin/env python3
"""validate_plugin.py — official-schema + project-policy validation.

Validation model (design doc §35, §36):

    Official Agent Plugins schema (pinned local copy under schemas/)
        + project policy invariants (this file)

The official schemas answer "what is a valid Agent Plugins 1.0.0
plugin.json / mcp.json"; this validator adds ONLY project policy:

    plugin name == computer-use
    mcp.json declares exactly one stdio server 'cua-driver'
    it directly invokes: cua-driver mcp (no wrapper, no bundled runtime)
    fixed skill layout (skills/cua-driver only)
    thin Skill frontmatter conformance (§37)
    no unknown local runtime components (§5, §67)

We deliberately do NOT re-implement the official schema; field-set
checking comes from the pinned copies. No network access: the schemas are
committed, never fetched at validation time.

Usage:
    python scripts/validate_plugin.py [--root PATH]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import jsonschema

from verify_upstream import _parse_frontmatter

PLUGIN_NAME = "computer-use"
SERVER_NAME = "cua-driver"
SKILL_NAME = "cua-driver"

SCHEMA_REL = Path("schemas") / "agent-plugins" / "1.0.0"
PLUGIN_SCHEMA_REL = SCHEMA_REL / "plugin.schema.json"
MCP_SCHEMA_REL = SCHEMA_REL / "mcp.schema.json"

# Project policy (§10): these manifest keys are forbidden in this plugin even
# if a future schema revision were to permit them. Skills/MCP are discovered
# from fixed directories; runtime knobs do not belong in the manifest.
PLUGIN_FORBIDDEN_KEYS = {
    "cuaVersion",
    "runtime",
    "permissions",
    "tools",
    "platform",
    "computerUse",
    "mcpServers",
    "skills",
}

FORBIDDEN_COMMAND_PATTERN = re.compile(
    r"(\bbash\b|\bsh\b|\bzsh\b|\bpwsh\b|\bpowershell\b|\bnode\b|\bnpm\b|"
    r"\bpython\b|\bpython3\b|\buv\b|\bcurl\b|\bwr?get\b|computer-use-server)"
)

# Agent Skills standard frontmatter fields.
SKILL_ALLOWED_FIELDS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$")
DESCRIPTION_MAX = 1024

# Forbidden runtime component names (design doc §5) and suspicious wrapper
# entry points that would smuggle a second runtime into the plugin.
FORBIDDEN_DIRS = {"bin", "dist", "build", "out", "node_modules", "vendor-bin"}
FORBIDDEN_ENTRY_FILES = {
    "proxy.js",
    "proxy.py",
    "wrapper.py",
    "wrapper.js",
    "server.js",
    "server.py",
    "computer-use-server",
    "computer-use-server.py",
    "computer-use-server.js",
    "computer_use.py",
}


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


def _add(checks: list[Check], name: str, ok: bool, detail: str = "") -> None:
    checks.append(Check(name, ok, detail))


def _load_schema(root: Path, rel: Path):
    return json.loads((root / rel).read_text(encoding="utf-8"))


def _schema_errors(schema: dict, instance) -> list[str]:
    validator = jsonschema.Draft202012Validator(schema)
    return [
        f"{'/'.join(str(p) for p in error.absolute_path) or '<root>'}: {error.message}"
        for error in sorted(validator.iter_errors(instance), key=str)
    ]


def _check_against_official_schema(root: Path, doc_rel: Path, schema_rel: Path,
                                    label: str, checks: list[Check]) -> dict | None:
    """Load doc, validate against the pinned official schema, return the doc."""
    if not (root / schema_rel).is_file():
        _add(checks, f"{label}: pinned official schema present", False, str(schema_rel))
    if not (root / doc_rel).is_file():
        _add(checks, f"{label} exists", False, str(doc_rel))
        return None
    _add(checks, f"{label} exists", True)
    try:
        doc = json.loads((root / doc_rel).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _add(checks, f"{label} parses", False, str(exc))
        return None
    _add(checks, f"{label} parses", True)
    try:
        schema = _load_schema(root, schema_rel)
        errors = _schema_errors(schema, doc)
        _add(checks, f"{label} conforms to official Agent Plugins schema",
             not errors, "; ".join(errors[:3]) if errors else "pinned 1.0.0 schema")
    except (OSError, json.JSONDecodeError) as exc:
        _add(checks, f"{label} conforms to official Agent Plugins schema",
             False, f"cannot load pinned schema: {exc}")
    return doc


def _check_plugin_policy(manifest: dict | None, checks: list[Check]) -> None:
    if not isinstance(manifest, dict):
        return
    _add(checks, "policy: plugin name == computer-use",
         manifest.get("name") == PLUGIN_NAME, repr(manifest.get("name")))
    _add(checks, "policy: plugin version is semver",
         bool(SEMVER_PATTERN.match(manifest.get("version", ""))),
         repr(manifest.get("version")))
    _add(checks, "policy: plugin description non-empty",
         isinstance(manifest.get("description"), str)
         and bool(manifest.get("description", "").strip()))
    forbidden = sorted(PLUGIN_FORBIDDEN_KEYS & manifest.keys())
    _add(checks, "policy: no forbidden manifest keys", not forbidden,
         f"forbidden: {forbidden}" if forbidden else "")
    _add(checks, "policy: repository points at a real GitHub origin",
         isinstance(manifest.get("repository"), str)
         and re.match(r"^https://github\.com/[^/]+/[^/]+$", manifest.get("repository", "")),
         repr(manifest.get("repository")))


def _check_mcp_policy(doc: dict | None, checks: list[Check]) -> None:
    if not isinstance(doc, dict):
        return
    servers = doc.get("mcpServers")
    if not isinstance(servers, dict) or set(servers) != {SERVER_NAME}:
        _add(checks, "policy: mcp.json declares exactly server 'cua-driver'", False,
             f"servers: {sorted(servers) if isinstance(servers, dict) else type(servers).__name__}")
        return
    entry = servers[SERVER_NAME]
    if not isinstance(entry, dict):
        return
    _add(checks, "policy: mcp server name == cua-driver", True)
    _add(checks, "policy: transport type == stdio", entry.get("type") == "stdio")
    command = entry.get("command", "")
    _add(checks, "policy: command directly invokes cua-driver",
         command == "cua-driver", repr(command))
    _add(checks, "policy: args == ['mcp']", entry.get("args") == ["mcp"],
         repr(entry.get("args")))
    wrapper_match = FORBIDDEN_COMMAND_PATTERN.search(str(command))
    _add(checks, "policy: no wrapper/interpreter command",
         not wrapper_match,
         f"command matches forbidden pattern: {command!r}" if wrapper_match else repr(command))
    wrapper_in_args = any(
        FORBIDDEN_COMMAND_PATTERN.search(str(value))
        for value in entry.get("args", []) if isinstance(entry.get("args"), list)
    )
    _add(checks, "policy: no wrapper/interpreter in args", not wrapper_in_args)
    _add(checks, "policy: command is not a local path",
         "/" not in command and "\\" not in command and not command.startswith("."))
    local_ref = str(command).lstrip().startswith((".", "/", "~")) or ("cwd" in entry)
    _add(checks, "policy: mcp.json references no bundled runtime", not local_ref)


def _check_skill(root: Path, checks: list[Check]) -> None:
    skills_dir = root / "skills"
    if not skills_dir.is_dir():
        _add(checks, "skills/ exists", False)
        return
    _add(checks, "skills/ exists", True)
    subdirs = sorted(p.name for p in skills_dir.iterdir() if p.is_dir())
    _add(checks, "skills contains only 'cua-driver'", subdirs == [SKILL_NAME], f"dirs: {subdirs}")
    skill_path = skills_dir / SKILL_NAME / "SKILL.md"
    if not skill_path.is_file():
        _add(checks, "thin SKILL.md exists", False, str(skill_path))
        return
    _add(checks, "thin SKILL.md exists", True)
    mirror = skills_dir / SKILL_NAME / "references" / "upstream" / "SKILL.md"
    _add(checks, "upstream mirror SKILL.md exists", mirror.is_file(), str(mirror))

    fm, err = _parse_frontmatter(skill_path.read_text(encoding="utf-8"))
    if err or not isinstance(fm, dict):
        _add(checks, "thin SKILL.md frontmatter parses", False, err or "parse error")
        return
    _add(checks, "thin SKILL.md frontmatter parses", True)

    name = fm.get("name")
    _add(checks, "skill name present", isinstance(name, str) and bool(name), repr(name))
    _add(checks, "skill name matches directory", name == SKILL_NAME, repr(name))
    _add(checks, "skill name charset valid", bool(SKILL_NAME_PATTERN.match(str(name))))

    description = fm.get("description")
    _add(checks, "skill description non-empty",
         isinstance(description, str) and bool(description.strip()))
    _add(checks, "skill description <= 1024 chars",
         isinstance(description, str) and len(description) <= DESCRIPTION_MAX,
         f"{len(description) if isinstance(description, str) else 0} chars")

    for field in ("license", "compatibility"):
        value = fm.get(field)
        _add(checks, f"skill {field} valid (optional, string)",
             value is None or (isinstance(value, str) and bool(value.strip())),
             repr(value))

    metadata = fm.get("metadata")
    if metadata is None:
        _add(checks, "skill metadata mapping (optional)", True, "absent")
    elif isinstance(metadata, dict):
        bad = [k for k, v in metadata.items() if not isinstance(v, str)]
        _add(checks, "metadata values are all strings", not bad, f"non-string: {bad}")
    else:
        _add(checks, "skill metadata mapping (optional)", False, type(metadata).__name__)

    unknown_fields = sorted(set(fm) - SKILL_ALLOWED_FIELDS)
    _add(checks, "no unknown frontmatter fields", not unknown_fields, f"unknown: {unknown_fields}")


def _check_runtime_surface(root: Path, checks: list[Check]) -> None:
    for forbidden_dir in FORBIDDEN_DIRS:
        _add(checks, f"no local runtime dir '{forbidden_dir}/'",
             not (root / forbidden_dir).exists())
    hits: list[str] = []
    for candidate in FORBIDDEN_ENTRY_FILES:
        for location in (root, root / "skills", root / "skills" / SKILL_NAME):
            if (location / candidate).exists():
                hits.append(str((location / candidate).relative_to(root)))
    _add(checks, "no wrapper/proxy/server entry files", not hits, f"found: {hits}" if hits else "")

    stray = [
        str(p.relative_to(root))
        for pattern in ("*.py", "*.js", "*.mjs", "*.cjs")
        for p in root.glob(pattern)
    ]
    _add(checks, "no executable code at plugin root", not stray, f"stray: {stray}" if stray else "")


def run_checks(root: Path) -> list[Check]:
    checks: list[Check] = []
    manifest = _check_against_official_schema(
        root, Path("plugin.json"), PLUGIN_SCHEMA_REL, "plugin.json", checks)
    mcp_doc = _check_against_official_schema(
        root, Path("mcp.json"), MCP_SCHEMA_REL, "mcp.json", checks)
    _check_plugin_policy(manifest, checks)
    _check_mcp_policy(mcp_doc, checks)
    _check_skill(root, checks)
    _check_runtime_surface(root, checks)
    return checks


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=None, help="repository root")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1]

    checks = run_checks(root)
    width = max(len(c.name) for c in checks)
    failed = 0
    for check in checks:
        status = "PASS" if check.ok else "FAIL"
        line = f"[{status}] {check.name.ljust(width)}"
        if check.detail:
            line += f"  ({check.detail})"
        print(line)
        failed += not check.ok

    if failed:
        print(f"\nFAIL: {failed} plugin validation check(s) failed.")
        return 1
    print(f"\nAll {len(checks)} plugin validation checks passed "
          "(official pinned schemas + project policy, offline).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
