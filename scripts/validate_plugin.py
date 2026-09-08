#!/usr/bin/env python3
"""validate_plugin.py — deterministic Agent Plugins / Agent Skills validation.

Development-time structural validation with NO network access and NO schema
downloads (schemas are only fetched as pinned local copies at build time if
ever needed; this validator is deliberately self-contained — design doc §35,
§36). It checks:

    plugin.json            closed core schema, name, forbidden keys
    mcp.json               single stdio server "cua-driver" -> cua-driver mcp
    fixed paths            skills/cua-driver/SKILL.md; only skill dir
    thin Skill frontmatter name/description/license/compatibility/metadata
                           rules (§37), no unknown fields
    runtime surface        no unknown local runtime components (§5, §67):
                           no bin/, no wrapper/proxy/server entry points

Usage:
    python scripts/validate_plugin.py [--root PATH] [--strict-skill]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from verify_upstream import _parse_frontmatter

PLUGIN_NAME = "computer-use"
SERVER_NAME = "cua-driver"
SKILL_NAME = "cua-driver"

# Closed core schema of Agent Plugins 1.0.0 manifests: a fixed field set.
PLUGIN_ALLOWED_KEYS = {
    "$schema",
    "name",
    "version",
    "description",
    "keywords",
    "repository",
    "author",
    "homepage",
    "license",
    "links",
}
# Explicitly forbidden manifest keys (design doc §10) — Skills/MCP are
# discovered from fixed directories, runtime knobs do not belong here.
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

MCP_ENTRY_ALLOWED_KEYS = {"type", "command", "args", "env", "cwd"}
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


def _check_plugin_json(root: Path, checks: list[Check]) -> None:
    path = root / "plugin.json"
    if not path.is_file():
        _add(checks, "plugin.json exists", False, str(path))
        return
    _add(checks, "plugin.json exists", True)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _add(checks, "plugin.json parses", False, str(exc))
        return
    if not isinstance(manifest, dict):
        _add(checks, "plugin.json is an object", False)
        return
    _add(checks, "plugin.json parses", True)

    _add(checks, "plugin name == computer-use", manifest.get("name") == PLUGIN_NAME,
         repr(manifest.get("name")))
    _add(checks, "plugin version is semver", bool(SEMVER_PATTERN.match(manifest.get("version", ""))),
         repr(manifest.get("version")))
    description = manifest.get("description")
    _add(checks, "plugin description non-empty", isinstance(description, str) and bool(description.strip()))
    _add(checks, "plugin $schema pinned to 1.0.0",
         manifest.get("$schema") == "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
         repr(manifest.get("$schema")))

    forbidden = sorted(PLUGIN_FORBIDDEN_KEYS & manifest.keys())
    _add(checks, "no forbidden manifest keys", not forbidden, f"forbidden: {forbidden}")
    unknown = sorted(manifest.keys() - PLUGIN_ALLOWED_KEYS)
    _add(checks, "no unknown manifest keys", not unknown, f"unknown: {unknown}")


def _check_mcp_json(root: Path, checks: list[Check]) -> None:
    path = root / "mcp.json"
    if not path.is_file():
        _add(checks, "mcp.json exists", False, str(path))
        return
    _add(checks, "mcp.json exists", True)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _add(checks, "mcp.json parses", False, str(exc))
        return
    servers = doc.get("mcpServers") if isinstance(doc, dict) else None
    if not isinstance(servers, dict) or set(servers) != {SERVER_NAME}:
        _add(checks, "mcp.json declares exactly server 'cua-driver'", False,
             f"servers: {sorted(servers) if isinstance(servers, dict) else type(servers).__name__}")
        return
    entry = servers[SERVER_NAME]
    if not isinstance(entry, dict):
        _add(checks, "server entry is an object", False)
        return

    _add(checks, "mcp server name == cua-driver", True)
    _add(checks, "transport type == stdio", entry.get("type") == "stdio")
    command = entry.get("command", "")
    _add(checks, "command directly invokes cua-driver", command == "cua-driver", repr(command))
    _add(checks, "args == ['mcp']", entry.get("args") == ["mcp"], repr(entry.get("args")))
    wrapper_match = FORBIDDEN_COMMAND_PATTERN.search(str(command))
    _add(checks, "no wrapper/interpreter command",
         not wrapper_match,
         f"command matches forbidden pattern: {command!r}" if wrapper_match else repr(command))
    for value in entry.get("args", []) if isinstance(entry.get("args"), list) else []:
        if FORBIDDEN_COMMAND_PATTERN.search(str(value)):
            _add(checks, "no wrapper/interpreter in args", False, repr(value))
            break
    else:
        _add(checks, "no wrapper/interpreter in args", True)
    _add(checks, "command is not a local path", "/" not in command and "\\" not in command and
         not command.startswith("."))
    unknown_keys = sorted(set(entry) - MCP_ENTRY_ALLOWED_KEYS)
    _add(checks, "no unknown server-entry keys", not unknown_keys, f"unknown: {unknown_keys}")


def _check_skill(root: Path, checks: list[Check]) -> None:
    skills_dir = root / "skills"
    if not skills_dir.is_dir():
        _add(checks, "skills/ exists", False)
        return
    _add(checks, "skills/ exists", True)
    subdirs = sorted(p.name for p in skills_dir.iterdir() if p.is_dir())
    _add(checks, "skills contains only 'cua-driver'", subdirs == [SKILL_NAME], f"dirs: {subdirs}")
    # No stray SKILL.md at other depths is discoverable by the host, but the
    # mirror copy under references/upstream is expected to exist.
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
    _add(checks, "no wrapper/proxy/server entry files", not hits, f"found: {hits}")

    # Python/JS may only live in development areas (scripts/, tests/).
    stray = [
        str(p.relative_to(root))
        for pattern in ("*.py", "*.js", "*.mjs", "*.cjs")
        for p in root.glob(pattern)
    ]
    _add(checks, "no executable code at plugin root", not stray, f"stray: {stray}")

    # mcp.json must not reference any file inside the package (external runtime).
    try:
        mcp = json.loads((root / "mcp.json").read_text(encoding="utf-8"))
        entry = mcp.get("mcpServers", {}).get(SERVER_NAME, {})
        local_ref = str(entry.get("command", "")).lstrip().startswith((".", "/", "~")) or (
            "cwd" in entry
        )
        _add(checks, "mcp.json references no bundled runtime", not local_ref)
    except (OSError, json.JSONDecodeError, AttributeError):
        _add(checks, "mcp.json references no bundled runtime", True, "mcp.json already failed above")


def run_checks(root: Path) -> list[Check]:
    checks: list[Check] = []
    _check_plugin_json(root, checks)
    _check_mcp_json(root, checks)
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
    print(f"\nAll {len(checks)} plugin validation checks passed (offline, deterministic).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
