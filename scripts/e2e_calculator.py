#!/usr/bin/env python3
"""e2e_calculator.py — Level 3 desktop qualification: Calculator 6 × 7 = 42.

The first real GUI qualification (design doc §50-§53). It proves the Cua
semantic desktop path end to end: session, app discovery, launch, window
targeting, structured accessibility state, MCP image content, semantic
clicks, and mutation verification.

Safety / rules:
    - DRY RUN by default; real desktop interaction requires --yes (§53) so
      CI, test discovery, or accidental imports can never touch a desktop.
    - SEMANTIC-ONLY interaction: elements are located by accessibility
      name/role and activated through their semantic token. Blind pixel
      clicks (x/y coordinates) are never used, so a semantic-path failure
      is a qualification FAIL, not something to work around (§52).

Usage:
    python scripts/e2e_calculator.py            # plan only (dry run)
    python scripts/e2e_calculator.py --yes      # real desktop qualification
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mcp_client  # noqa: E402  (development tool dependency)

# Button label candidates per platform/locale (English Windows uses "Six",
# "Multiply", "Equals"; zh-CN uses "六", "乘以", "等于"; others "6", "×", "=").
BUTTONS: dict[str, tuple[str, ...]] = {
    "6": ("6", "six", "六"),
    "*": ("×", "x", "*", "multiply", "times", "multiply by", "乘以", "乘"),
    "7": ("7", "seven", "七"),
    "=": ("=", "equals", "equal", "is equal to", "等于"),
}
CLICKABLE_HINTS = ("button", "press", "invoke", "click")
TOKEN_KEYS = ("element_token", "token", "element", "ref", "ax_token", "elementId", "element_id", "id")
TEXT_KEYS = ("name", "title", "label", "value", "text", "role")
DISPLAY_HINTS = ("display", "result", "expression", "显示", "结果")
CLOSE_BUTTON_LABELS = ("关闭 计算器", "关闭", "close calculator", "close")


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--yes", action="store_true",
                        help="actually drive the desktop (without it: dry run)")
    parser.add_argument("--command", default="cua-driver")
    parser.add_argument("--app", default="calc,计算器",
                        help="comma-separated substrings used to discover the calculator app "
                             "(default: 'calc,计算器' — English and zh-CN system names)")
    parser.add_argument("--expect", default="42", help="expected displayed result")
    parser.add_argument("--settle", type=float, default=1.0,
                        help="seconds to wait after launch / each click")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--root", default=None, help="repository root")
    return parser.parse_args(argv)


# --------------------------------------------------------------- state utils


def collect_elements(node, out: list | None = None) -> list[dict]:
    """Depth-first collection of every mapping that looks like an element."""
    if out is None:
        out = []
    if isinstance(node, dict):
        if any(key in node for key in ("name", "role", "label")) or any(
            key in node for key in TOKEN_KEYS
        ):
            out.append(node)
        for value in node.values():
            collect_elements(value, out)
    elif isinstance(node, list):
        for item in node:
            collect_elements(item, out)
    return out


def element_text(element: dict) -> str:
    return " ".join(
        str(element[key]) for key in TEXT_KEYS if element.get(key) is not None
    )


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def element_token(element: dict):
    for key in TOKEN_KEYS:
        value = element.get(key)
        if value is not None and not isinstance(value, (dict, list)):
            return key, value
    return None


def looks_clickable(element: dict) -> bool:
    role = normalize(str(element.get("role", "")))
    text = normalize(element_text(element))
    return any(hint in role or hint in text for hint in CLICKABLE_HINTS)


def _element_labels(element: dict) -> set[str]:
    """Normalized label set: each text field separately plus the combined text."""
    labels = set()
    for key in TEXT_KEYS:
        value = element.get(key)
        if isinstance(value, str) and value.strip():
            labels.add(normalize(value))
    combined = normalize(element_text(element))
    if combined:
        labels.add(combined)
    return labels


def find_button(elements: list[dict], labels: tuple[str, ...]) -> dict:
    """Locate a calculator button semantically (exact normalized match first)."""
    wanted = {normalize(label) for label in labels}
    matches = [element for element in elements if wanted & _element_labels(element)]
    if not matches:
        raise AssertionError(
            f"no element matching {sorted(wanted)} in window state; "
            "semantic path failed (blind pixel fallback is not allowed)"
        )
    for element in matches:
        if looks_clickable(element):
            return element
    return matches[0]


def find_display_text(elements: list[dict]) -> str:
    for element in elements:
        text = element_text(element)
        if any(hint in normalize(text) for hint in DISPLAY_HINTS):
            return text
    return ""


def build_args(schema: dict | None, candidates: dict) -> dict:
    """Build tool arguments from an ordered candidate map, schema-aware.

    Only candidate keys that the tool's inputSchema declares are used; this
    keeps the qualification script meaningful across harmless upstream
    parameter renames while still failing closed on real contract drift.
    """
    properties = (schema or {}).get("properties") or {}
    args = {key: value for key, value in candidates.items() if key in properties}
    missing_required = [
        key for key in (schema or {}).get("required", []) if key not in args
    ]
    if missing_required:
        raise AssertionError(
            f"tool schema requires {missing_required}, but no semantic candidate "
            f"maps to it (schema drift — manual review required); schema={schema}"
        )
    return args


def tool_schema(tools: list[dict], name: str) -> dict | None:
    for tool in tools:
        if tool.get("name") == name:
            return tool.get("inputSchema")
    return None


def extract_windows(payload: dict) -> list[dict]:
    windows = collect_candidates(payload, "windows")
    return [w for w in windows if isinstance(w, dict)]


def extract_apps(payload: dict) -> list[dict]:
    apps = collect_candidates(payload, "apps")
    return [a for a in apps if isinstance(a, dict)]


def collect_candidates(node, key: str) -> list:
    """Find the first list-of-dicts stored under `key` anywhere in `node`."""
    if isinstance(node, dict):
        value = node.get(key)
        if isinstance(value, list) and value:
            return value
        for child in node.values():
            found = collect_candidates(child, key)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = collect_candidates(item, key)
            if found:
                return found
    return []


def window_id(window: dict):
    for key in ("window_id", "windowId", "id", "windowId", "wid"):
        if window.get(key) is not None:
            return window[key]
    return None


def find_session_id(node) -> str | None:
    """Locate a scalar session identifier anywhere in a tool result."""
    if isinstance(node, dict):
        for key in ("session", "session_id", "sessionId", "id"):
            value = node.get(key)
            if key in ("session", "session_id", "sessionId") and value is not None and not isinstance(value, (dict, list)):
                return value
        for value in node.values():
            found = find_session_id(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = find_session_id(item)
            if found is not None:
                return found
    return None


def result_payload(result: dict) -> dict:
    """Normalized view: structuredContent preferred, text content included."""
    payload = dict(result.get("structuredContent") or {})
    payload["_content"] = result.get("content") or []
    return payload


# ------------------------------------------------------------------- steps


def plan_only(args) -> int:
    print("DRY RUN — Calculator qualification plan (design doc §51):\n")
    steps = [
        "1.  start_session",
        f"2.  discover Calculator app (list_apps, match {args.app!r})",
        "3.  launch_app (semantically)",
        "4.  select the exact Calculator window (list_windows)",
        "5.  get_window_state -> assert structured accessibility elements",
        "6.  get_window_state -> assert MCP image content is returned",
        "7.  click '6' via semantic element token -> fresh state",
        "8.  click '×' via semantic element token -> fresh state",
        "9.  click '7' via semantic element token -> fresh state",
        "10. click '=' via semantic element token -> fresh state",
        f"11. verify display shows {args.expect!r} (semantic read, no pixel OCR)",
        "12. end_session",
    ]
    print("\n".join(steps))
    print(
        "\nNo desktop interaction happened. Re-run with --yes on a real "
        "interactive desktop with cua-driver installed to execute this plan."
    )
    return 0


def cleanup_launched_app(client, schemas: dict, needles: list[str], timeout: float,
                         launch_pid=None, owned_window_ids: set | None = None) -> None:
    """Best-effort, semantic-only cleanup of what THIS run launched.

    Ownership is proven by the launch result (real app pid) or by the
    pre/post window-set diff (owned window ids). We never guess a
    calculator by name: if ownership cannot be proven, we warn and close
    nothing. The UWP host pid from window objects is never used for
    kill_app — it hosts other apps too.

    Paths (both semantic, no pixel fallback):
      1. app still running at the launch-returned pid -> kill_app
      2. owned window still on screen -> title-bar close button, following
         the delivery_mode contract (background first; escalate to
         foreground only on structured rejection or verified no-op)
    Failures warn but never flip a PASS into a FAIL.
    """
    owned_window_ids = owned_window_ids or set()

    # 1. running app with the launch-returned pid (the real app pid)
    if launch_pid:
        try:
            apps = extract_apps(result_payload(client.tools_call(
                "list_apps", build_args(schemas.get("list_apps"), {}), timeout=timeout)))
            entry = next((a for a in apps if a.get("pid") == launch_pid), None)
            if entry:
                client.tools_call("kill_app", {"pid": launch_pid}, timeout=30.0)
                print(f"cleanup: kill_app OK ({entry.get('name')!r}, pid={launch_pid})")
                return
        except Exception as exc:
            print(f"WARNING: cleanup list_apps failed: {exc}")

    # 2. owned leftover window (e.g. suspended UWP) -> title-bar close button
    try:
        windows = extract_windows(result_payload(client.tools_call(
            "list_windows", build_args(schemas.get("list_windows"), {}), timeout=timeout)))
        calc = [w for w in windows if window_id(w) in owned_window_ids]
        if not calc:
            if launch_pid is None and not owned_window_ids:
                print("cleanup: cannot prove ownership (no launch pid, no owned "
                      "windows); not closing anything")
            else:
                print("cleanup: nothing left to close")
            return
        w = calc[0]
        wargs = {
            "window_id": window_id(w),
            "windowId": window_id(w),
            "id": window_id(w),
            "pid": w.get("pid"),
        }
        state = result_payload(client.tools_call(
            "get_window_state", build_args(schemas.get("get_window_state"), wargs),
            timeout=timeout))
        button = find_button(collect_elements(state), CLOSE_BUTTON_LABELS)
        token = element_token(button)
        if token is None:
            raise AssertionError(
                "close-button element_token missing; refusing a name-only click")

        def click_close(delivery_mode: str | None = None) -> None:
            click_args = dict(wargs)
            if token:
                click_args[token[0]] = token[1]
            if delivery_mode:
                click_args["delivery_mode"] = delivery_mode
            client.tools_call(
                "click", build_args(schemas.get("click"), click_args), timeout=30.0)

        def owned_remaining() -> list:
            """§33: check cleanup only against owned window ids — a
            pre-existing calculator elsewhere on the desktop is not our
            concern and must not flip this run's result."""
            wins = extract_windows(result_payload(client.tools_call(
                "list_windows", build_args(schemas.get("list_windows"), {}), timeout=timeout)))
            return [w2 for w2 in wins if window_id(w2) in owned_window_ids]

        need_escalation = False
        try:
            click_close()  # background: mandatory first attempt
            time.sleep(1.5)  # UWP close may lag behind the unverifiable effect
            need_escalation = bool(owned_remaining())
        except mcp_client.McpError as exc:
            # structured background rejection (e.g. background_unavailable):
            # the delivery_mode contract says re-issue the same action with
            # 'foreground' exactly now.
            print(f"cleanup: background click rejected by driver ({exc}); escalating")
            need_escalation = True
        if need_escalation:
            try:
                client.tools_call(
                    "bring_to_front", build_args(schemas.get("bring_to_front"), wargs),
                    timeout=30.0)
            except Exception:
                pass  # best-effort wake-up; the foreground click may still work
            try:
                click_close("foreground")
            except mcp_client.McpError as exc:
                if not any(word in str(exc).lower() for word in ("closed", "stale", "invalid")):
                    raise
                # the window vanished mid-escalation: the background click
                # had actually worked; fall through and re-check.
            time.sleep(1.5)
            remaining = owned_remaining()
        else:
            remaining = []
        if remaining:
            print(f"WARNING: cleanup clicked close but {len(remaining)} calculator "
                  "window(s) remain — close manually")
        else:
            print("cleanup: window closed via title-bar close button "
                  "(semantic click, background->foreground escalation)")
    except Exception as exc:
        print(f"WARNING: cleanup failed (close the calculator manually): {exc}")


def run_qualification(args) -> int:
    failures: list[str] = []
    session_started = False
    launched = False
    launch_pid = None
    owned_window_ids: set = set()
    needles = [n.strip().lower() for n in args.app.split(",") if n.strip()]
    client: mcp_client.McpStdioClient | None = None
    schemas: dict = {}

    resolved = shutil.which(args.command)
    if not resolved:
        print(f"FAIL: {args.command!r} not found on PATH.")
        return 1

    try:
        client = mcp_client.McpStdioClient(resolved, ["mcp"])
        client.start()
        client.initialize(timeout=args.timeout)
        tools = client.tools_list(timeout=args.timeout)
        schemas = {tool.get("name"): tool.get("inputSchema") for tool in tools}
        print(f"MCP OK: {len(tools)} tools")

        # 1. session ------------------------------------------------------
        start_result = client.tools_call(
            "start_session", build_args(schemas.get("start_session"), {}), timeout=args.timeout
        )
        session_started = True
        session_id = find_session_id(result_payload(start_result))
        print(f"start_session OK (session={session_id})")

        # 2. discover calculator app ---------------------------------------
        apps_result = client.tools_call("list_apps", build_args(schemas.get("list_apps"), {}),
                                        timeout=args.timeout)
        apps = extract_apps(result_payload(apps_result))
        matches = [a for a in apps if any(n in element_text(a).lower() for n in needles)]
        if not matches:
            raise AssertionError(
                f"no app matching {needles} in list_apps output "
                f"({[element_text(a) for a in apps][:10]} …)"
            )
        app = matches[0]
        app_name = next((app[k] for k in ("name", "app", "bundle_id") if app.get(k)), None)
        print(f"discovered app: {app_name!r}")

        # 3. launch with ownership tracking (close only what we launch) ------
        pre_windows = extract_windows(result_payload(client.tools_call(
            "list_windows", build_args(schemas.get("list_windows"), {}), timeout=args.timeout)))
        baseline_ids = {(w.get("pid"), window_id(w)) for w in pre_windows}

        launch_candidates = {
            k: app[k]
            for k in ("name", "app", "bundle_id", "aumid", "path", "launch_path")
            if app.get(k)
        }
        launch_candidates.setdefault("session", session_id)
        launch_result = client.tools_call(
            "launch_app", build_args(schemas.get("launch_app"), launch_candidates),
            timeout=args.timeout)
        launched = True
        launch_payload = result_payload(launch_result)
        launch_pid = launch_payload.get("pid") if isinstance(
            launch_payload.get("pid"), (int, str)) else None
        print(f"launch_app OK ({app_name!r}, launch pid={launch_pid})")
        time.sleep(args.settle)

        # 4. exact window — only one this run can prove it created -----------
        post_windows = extract_windows(result_payload(client.tools_call(
            "list_windows", build_args(schemas.get("list_windows"), {}), timeout=args.timeout)))
        new_windows = [
            w for w in post_windows if (w.get("pid"), window_id(w)) not in baseline_ids
        ]
        calc_windows = [
            w for w in new_windows if any(n in element_text(w).lower() for n in needles)
        ]
        if not calc_windows:
            raise AssertionError(
                "launch did not produce an identifiable new calculator window "
                f"(new windows: {[element_text(w) for w in new_windows]}); refusing "
                "to act on pre-existing calculator windows"
            )
        window = calc_windows[0]
        wid = window_id(window)
        # Ownership (§32): ONLY the explicitly selected target window is
        # owned — not every new window (another app may have opened one).
        owned_window_ids = {wid}
        print(f"window selected (owned): {element_text(window)!r} "
              f"(id={wid}, pid={window.get('pid')})")

        window_args = {
            "window_id": wid,
            "windowId": wid,
            "id": wid,
            "pid": window.get("pid"),
            "session": session_id,
        }
        state_args = {
            "include_accessibility_tree": True,
            "include_screenshot": True,
            **window_args,
        }
        get_window = lambda: result_payload(client.tools_call(
            "get_window_state",
            build_args(schemas.get("get_window_state"), state_args),
            timeout=args.timeout,
        ))

        # 5/6. structured elements + image content ----------------------------
        state = get_window()
        elements = collect_elements(state)
        if not elements:
            raise AssertionError("get_window_state returned no structured elements")
        print(f"structured elements OK ({len(elements)})")

        has_image = any(
            isinstance(item, dict) and item.get("type") == "image"
            for item in state.get("_content", [])
        )
        if has_image:
            print("image content OK")
        else:
            failures.append("get_window_state returned no MCP image content")

        # 7-10. semantic click sequence ---------------------------------------
        for step, key in enumerate(("6", "*", "7", "="), start=7):
            state = get_window()  # fresh state before each action (§51)
            elements = collect_elements(state)
            button = find_button(elements, BUTTONS[key])
            token = element_token(button)
            if token is None:
                # Qualification claims snapshot-bound semantic handles (see
                # compatibility.json receipt); name-only targeting is not
                # sufficient evidence, so fail closed here.
                raise AssertionError(
                    f"element_token missing for button {key!r} — qualification "
                    "requires snapshot-bound semantic handles"
                )
            click_candidates: dict = {token[0]: token[1]}
            click_candidates.update({
                "name": button.get("name"),
                "text": button.get("name"),
                **window_args,
            })
            click_candidates = {k: v for k, v in click_candidates.items() if v is not None}
            click_args = build_args(schemas.get("click"), click_candidates)
            bad = [k for k in ("x", "y", "coordinate", "coordinates", "point") if k in click_args]
            if bad:  # defensive: semantic-only qualification (§52)
                raise AssertionError(f"click was about to use pixel coordinates: {bad}")
            client.tools_call("click", click_args, timeout=args.timeout)
            print(f"click {key!r} OK (semantic token {token})")
            time.sleep(args.settle)

        # 11. verify result ---------------------------------------------------
        state = get_window()
        display = find_display_text(collect_elements(state))
        print(f"display read: {display!r}")
        # Exact match with digit boundaries: '42' must not match '142'/'423'.
        expected = rf"(?<!\d){re.escape(args.expect)}(?!\d)"
        if not re.search(expected, display.replace(",", "")):
            failures.append(f"display does not show {args.expect!r} exactly (read: {display!r})")
        else:
            print(f"verify {args.expect} PASS (exact, digit-bounded)")

    except AssertionError as exc:
        failures.append(str(exc))
    except Exception as exc:  # qualification evidence, not a crash traceback
        failures.append(f"{type(exc).__name__}: {exc}")
    finally:
        # Test hygiene: close ONLY what this run launched, inside the session,
        # through the same semantic Cua path (see cleanup_launched_app).
        if client and launched:
            cleanup_launched_app(client, schemas, needles, args.timeout,
                                 launch_pid=launch_pid,
                                 owned_window_ids=owned_window_ids)
        if client and session_started:
            try:
                client.tools_call("end_session", {}, timeout=30.0)
                print("end_session OK")
            except Exception as exc:
                failures.append(f"end_session failed: {exc}")
        if client:
            client.close()

    if failures:
        print()
        for failure in failures:
            print(f"FAIL: {failure}")
        print("\nCalculator qualification FAILED.")
        return 1
    print("\nCalculator qualification PASS (Level 3, semantic-only).")
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)
    if not args.yes:
        return plan_only(args)
    return run_qualification(args)


if __name__ == "__main__":
    sys.exit(main())
