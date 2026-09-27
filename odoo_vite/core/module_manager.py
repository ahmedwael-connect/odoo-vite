"""Odoo module management (Sprint 6, Part B: Tickets B.1–B.6).

Design decision (per spec's required upfront call — documented here):
- List / Diff / Dependency Graph read DIRECT Postgres (`ir_module_module`
  + friends) plus on-disk manifests. Work whether the instance is running
  or not. DB is the source for state; disk is the source for versions.
- Install / Update(-u) / Uninstall / Shell are ONE-SHOT `odoo-bin`
  invocations (`-i`/`-u`/`shell ... --stop-after-init` style). They refuse
  when the instance is RUNNING ON THE TARGET database (two writers on one
  DB/filestore is the actual hazard — concurrent processes on *different*
  DBs only share read-only code, which Odoo handles fine). Stopped, or
  running-on-another-DB, is allowed. The rule is enforced, not assumed.
- Update's git step covers community_path ONLY: enterprise checkouts are the
  user's own clones/credentials and must never be pulled by us. Said loudly
  in the UI too.

No GTK imports.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from odoo_vite.core.instance import effective_python
from odoo_vite.core.result import Result


def _password_for(instance) -> str:  # type: ignore[no-untyped-def]
    from odoo_vite.core.registry import get_db_password

    try:
        return get_db_password(instance) or ""
    except Exception:
        return instance.db_password or ""


def _pg(instance, db_name: str, sql: str, timeout: int = 60):  # type: ignore[no-untyped-def]
    """Run read-only SQL as the instance's db user. Returns (rc, stdout)."""
    env = dict(os.environ)
    pw = _password_for(instance)
    if pw:
        env["PGPASSWORD"] = pw
    try:
        proc = subprocess.run(
            ["psql", "-h", "localhost", "-U", instance.db_user or "odoo",
             "-d", db_name, "-tAc", sql],
            capture_output=True, text=True, timeout=timeout, env=env)
        out = proc.stdout.strip()
        if proc.returncode != 0 and proc.stderr.strip():
            out = (proc.stderr.strip() + ("\n" + out if out else ""))
        return proc.returncode, out
    except subprocess.TimeoutExpired:
        return -1, "timed out"
    except OSError as exc:
        return -1, f"cannot start psql: {exc}"


def _require_initialized(instance, db_name: str):  # type: ignore[no-untyped-def]
    """Guard: initialized DB or a clear 'go initialize first' failure."""
    from odoo_vite.core.db_state import get_db_state

    try:
        pw = _password_for(instance) or None
        state = get_db_state(db_name, instance.db_user or "odoo", pw)
    except Exception as exc:
        return None, Result.failure(f"Cannot inspect '{db_name}': {exc}")
    if state.error and not state.exists:
        return None, Result.failure(f"Cannot inspect '{db_name}': {state.error}")
    if not state.exists:
        return None, Result.failure(
            f"Database '{db_name}' does not exist — initialize it first "
            "(Databases tab → Initialize)")
    if not state.initialized:
        return None, Result.failure(
            f"Database '{db_name}' exists but is not initialized — run "
            "Initialize first (Databases tab)")
    return state, None


def _running_on(inst, db_name: str, db_path=None) -> int | None:  # type: ignore[no-untyped-def]
    """PID if live AND serving db_name (registry primary ≈ served db)."""
    from odoo_vite.core.process_manager import _alive_pid

    pid = _alive_pid(inst)
    if pid is None:
        return None
    primary = (inst.primary_db or "").strip()
    if primary and primary != db_name:
        return None  # running, but on another database — allowed
    return pid


def list_modules(instance, db_name: str, db_path=None) -> Result:  # type: ignore[no-untyped-def]
    """List modules from ir_module_module (B.1). Works stopped or running."""
    _state, err = _require_initialized(instance, db_name)
    if err is not None:
        return err
    # NOTE: no row_to_json — real-world shortdesc values break it, and
    # shortdesc's TYPE differs by version (JSONB with translations in 17.0,
    # plain text elsewhere). Single-column \x1f output; try JSONB extraction
    # first, fall back to plain text. Control chars are stripped in SQL.
    base_cols = ("name || chr(31) || state || chr(31) || "
                 "COALESCE(latest_version, '') || chr(31) || "
                 "COALESCE(published_version, '') || chr(31) || ")
    variants = [
        # [[:cntrl:]] covers NUL too (which cannot even be named in a literal).
        base_cols + ("regexp_replace(COALESCE(shortdesc->>'en_US', ''), "
                     "'[[:cntrl:]]', ' ', 'g')"),
        base_cols + ("regexp_replace(COALESCE(shortdesc::text, ''), "
                     "'[[:cntrl:]]', ' ', 'g')"),
    ]
    rc, out, last_err = -1, "", ""
    for select in variants:
        sql = f"SELECT {select} FROM ir_module_module ORDER BY name;"
        rc, out = _pg(instance, db_name, sql, timeout=120)
        if rc == 0:
            break
        last_err = out[:200]
    if rc != 0:
        return Result.failure(f"Cannot list modules in '{db_name}': {last_err}")
    modules = []
    for line in out.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 5:
            continue
        name, state, installed, available, summary = parts
        modules.append({"name": name, "state": state,
                        "installed_version": installed,
                        "available_version": available, "summary": summary})
    return Result.success(data={"modules": modules, "database": db_name},
                          message=f"{len(modules)} module(s) in '{db_name}'")


def _one_shot_preconditions(inst, db_name: str, db_path=None):  # type: ignore[no-untyped-def]
    """Shared guards for -i/-u/shell one-shots. Returns (ok_result_or_None)."""
    _state, err = _require_initialized(inst, db_name)
    if err is not None:
        return err
    pid = _running_on(inst, db_name, db_path)
    if pid is not None:
        return Result.failure(
            f"Instance '{inst.name}' is running on '{db_name}' (pid {pid}) — "
            "stop it first; a one-shot writer must never run concurrently "
            "with the server on the same database")
    _eff = effective_python(inst)
    venv_python = Path(_eff) if _eff else None
    odoo_bin = Path(inst.community_path) / "odoo-bin"
    if not venv_python:
        return Result.failure(
            f"No Python environment recorded for adopted instance '{inst.name}' — "
            "open its detail page and set the venv Python path, then retry")
    if not venv_python.is_file():
        return Result.failure(f"Venv python missing at {venv_python}")
    if not odoo_bin.is_file():
        return Result.failure(f"odoo-bin missing at {odoo_bin}")
    if inst.conf_path and not Path(inst.conf_path).is_file():
        return Result.failure(f"odoo.conf missing at {inst.conf_path}")
    return None


def install_modules(instance, db_name: str, module_names: list[str],  # type: ignore[no-untyped-def]
                    progress_cb=None, cancel=None, db_path=None) -> Result:
    """One-shot `odoo-bin -i mods --stop-after-init` (B.2)."""
    from odoo_vite.core.proc import run_streaming

    mods = [m.strip() for m in (module_names or []) if m.strip()]
    if not mods:
        return Result.failure("No modules selected to install")
    err = _one_shot_preconditions(instance, db_name, db_path)
    if err is not None:
        return err
    cmd = [effective_python(instance),
           str(Path(instance.community_path) / "odoo-bin"),
           "-c", instance.conf_path, "-d", db_name,
           "-i", ",".join(mods), "--stop-after-init"]
    res = run_streaming(cmd, progress_cb=progress_cb, cancel=cancel,
                        timeout=3600)
    if not res.ok:
        tail = "\n".join((res.data or {}).get("lines", [])[-15:])
        return Result.failure(
            f"Install of {', '.join(mods)} failed: {res.message}"
            + (f"\n--- log tail ---\n{tail}" if tail else ""))
    return Result.success(data={"modules": mods, "database": db_name},
                          message=f"Installed {', '.join(mods)} into '{db_name}'")


def update_code(instance, module_names: list[str], progress_cb=None,  # type: ignore[no-untyped-def]
                cancel=None, db_path=None, skip_pip: bool = False) -> Result:
    """B.3 full pipeline (B.3): git pull community → pip → -u --stop-after-init.

    Requires the instance STOPPED (code changes under a live server need a
    restart anyway; refusing is clearer than half-applying). Enterprise is
    deliberately never pulled (user-owned clone) — said in the log + UI.
    """
    from odoo_vite.core import venv_manager
    from odoo_vite.core.process_manager import _alive_pid

    mods = [m.strip() for m in (module_names or []) if m.strip()]
    if not mods:
        return Result.failure("No modules selected to update")

    def _emit(line: str) -> None:
        if progress_cb is not None:
            try:
                progress_cb(line)
            except Exception:
                pass

    if _alive_pid(instance) is not None:
        return Result.failure(
            f"Stop '{instance.name}' first — updating code under a running "
            "server leaves stale code in memory; restart after updating")
    community = Path(instance.community_path or "")
    if not (community / ".git").is_dir():
        return Result.failure(
            f"{community} is not a git checkout — cannot pull updates "
            "(adopted/non-git sources must be updated by hand)")

    _emit("=== [1/3] git pull (community only — enterprise is yours, skipping) ===")
    from odoo_vite.core.proc import run_streaming

    pulled = run_streaming(["git", "-C", str(community), "pull", "--ff-only"],
                           progress_cb=_emit, cancel=cancel, timeout=1200)
    if not pulled.ok:
        return Result.failure(f"Stage 1/3 git pull failed: {pulled.message}")

    if not skip_pip:
        _emit("=== [2/3] pip install -r requirements.txt ===")
        pip_res = venv_manager.install_requirements(
            Path(instance.venv_path), community,
            progress_cb=_emit, cancel=cancel)
        if not pip_res.ok:
            return Result.failure(f"Stage 2/3 pip install failed: {pip_res.message}")
    else:
        _emit("=== [2/3] pip step skipped (caller request) ===")

    db_name = (instance.primary_db or "").strip()
    if not db_name:
        return Result.failure("Instance has no primary database to update")
    _emit(f"=== [3/3] odoo-bin -u {','.join(mods)} on '{db_name}' ===")
    cmd = [effective_python(instance),
           str(community / "odoo-bin"), "-c", instance.conf_path,
           "-d", db_name, "-u", ",".join(mods), "--stop-after-init"]
    updated = run_streaming(cmd, progress_cb=_emit, cancel=cancel, timeout=3600)
    if not updated.ok:
        tail = "\n".join((updated.data or {}).get("lines", [])[-15:])
        return Result.failure(
            f"Stage 3/3 module update failed: {updated.message}"
            + (f"\n--- log tail ---\n{tail}" if tail else ""))
    _emit("Enterprise addons were NOT auto-updated (your own clone — pull it yourself).")
    return Result.success(data={"modules": mods, "database": db_name},
                          message=f"Updated {', '.join(mods)} (code+deps+data)")


def uninstall_modules(instance, db_name: str, module_names: list[str],
                      progress_cb=None, cancel=None, db_path=None) -> Result:
    """Uninstall via `odoo-bin shell` + button_immediate_uninstall (B.4).

    button_immediate_uninstall is stable across 15.0–19.0 (verified against
    each branch's ir_module.py) — no version-specific handling needed.
    """
    from odoo_vite.core.proc import run_streaming

    mods = [m.strip() for m in (module_names or []) if m.strip()]
    if not mods:
        return Result.failure("No modules selected to uninstall")
    err = _one_shot_preconditions(instance, db_name, db_path)
    if err is not None:
        return err
    names_literal = "[" + ", ".join(f"'{m}'" for m in mods) + "]"
    script = (
        "mods = env['ir.module.module'].search([('name', 'in', "
        f"{names_literal})])\n"
        f"missing = set({names_literal}) - set(mods.mapped('name'))\n"
        "notinstalled = mods.filtered(lambda m: m.state != 'installed')\n"
        "assert not missing, 'unknown modules: %s' % sorted(missing)\n"
        "assert not notinstalled, 'not installed: %s' % notinstalled.mapped('name')\n"
        "mods.button_immediate_uninstall()\n"
        "print('UNINSTALLED: ' + ','.join(sorted(mods.mapped('name'))))\n"
    )
    cmd = [effective_python(instance),
           str(Path(instance.community_path) / "odoo-bin"), "shell",
           "-c", instance.conf_path, "-d", db_name]
    res = run_streaming(cmd, progress_cb=progress_cb, cancel=cancel,
                        timeout=1800, stdin_text=script)
    if not res.ok:
        tail = "\n".join((res.data or {}).get("lines", [])[-15:])
        return Result.failure(
            f"Uninstall of {', '.join(mods)} failed: {res.message}"
            + (f"\n--- log tail ---\n{tail}" if tail else ""))
    lines = (res.data or {}).get("lines", []) or []
    done = [ln for ln in lines if ln.startswith("UNINSTALLED: ")]
    if not done:
        return Result.failure(
            "Uninstall ran but no confirmation marker found — refusing to "
            "claim success; check the log above")
    return Result.success(data={"modules": mods, "database": db_name},
                          message=f"Uninstalled {', '.join(mods)} from '{db_name}'")


def _ver_tuple(version: str) -> tuple:
    import re as _re

    return tuple(int(n) for n in _re.findall(r"\d+", str(version or "")))


def _adapt_version(version: str, series: str) -> str:
    """Mirror Odoo's adapt_version: framework manifests carry short versions
    ('1.3') meaning '<series>.1.3' — compare adapted forms, not raw strings."""
    version, series = (version or "").strip(), (series or "").strip()
    if not version:
        return version
    if not series or version == series or version.startswith(series + "."):
        return version
    return f"{series}.{version}"


def diff_modules(instance, db_name: str, db_path=None) -> Result:  # type: ignore[no-untyped-def]
    """Compare installed_version (db) vs on-disk manifest version (B.5)."""
    import ast

    res = list_modules(instance, db_name, db_path)
    if not res.ok:
        return res
    addons_roots: list[Path] = []
    # Note: framework modules (base, auth_totp, …) live in odoo/odoo/addons,
    # not in community/addons — both must be scanned.
    for root in [Path(instance.community_path) / "addons",
                 Path(instance.community_path) / "odoo" / "addons",
                 Path(instance.enterprise_path or ""),
                 Path(instance.custom_addons_path or "")]:
        if str(root) and root.is_dir():
            addons_roots.append(root)
    manifests: dict[str, str | None] = {}
    for root in addons_roots:
        try:
            children = sorted(root.iterdir())
        except OSError:
            continue
        for sub in children:
            if not sub.is_dir() or sub.name in manifests:
                continue
            mf = sub / "__manifest__.py"
            if not mf.is_file():
                continue
            # None = manifest file absent would skip above; "" = present
            # but with no version key (Odoo itself defaults those to 1.0).
            try:
                tree = ast.parse(mf.read_text(encoding="utf-8", errors="replace"))
                data = None
                for node in ast.walk(tree):
                    if isinstance(node, ast.Dict):
                        try:
                            cand = ast.literal_eval(node)
                        except (ValueError, SyntaxError):
                            continue
                        if isinstance(cand, dict) and cand.get("name"):
                            data = cand
                            break
                version = str((data or {}).get("version", ""))
            except (OSError, SyntaxError):
                version = ""
            manifests[sub.name] = version
            tech = str((data or {}).get("name", ""))
            if tech and tech not in manifests:
                manifests[tech] = version

    _MISSING = object()
    rows = []
    for mod in res.data["modules"]:
        if mod.get("state") != "installed":
            continue
        name, installed = mod.get("name", ""), mod.get("installed_version", "")
        disk = manifests.get(name, _MISSING)
        if disk is _MISSING:
            status, note = "manifest-missing", "installed but no manifest on disk"
        elif not disk:
            status, note = "unknown", "manifest has no version key — cannot compare"
        elif not installed:
            status, note = "unknown", "no installed version recorded"
        else:
            disk_adapted = _adapt_version(disk, instance.version)
            try:
                if _ver_tuple(disk_adapted) > _ver_tuple(installed):
                    status, note = "disk-newer", f"disk {disk} > db {installed} — needs update"
                elif _ver_tuple(disk_adapted) < _ver_tuple(installed):
                    status, note = "db-newer", f"db {installed} > disk {disk} — unexpected"
                else:
                    status, note = "in-sync", f"{installed}"
            except Exception:
                status, note = "unknown", f"db {installed} vs disk {disk}"
        rows.append({"name": name, "installed_version": installed,
                     "disk_version": disk, "status": status, "note": note,
                     "summary": mod.get("summary", "")})
    return Result.success(data={"diff": rows, "database": db_name},
                          message=f"{len(rows)} installed module(s) compared")


def get_dependency_graph(instance, db_name: str, db_path=None) -> Result:  # type: ignore[no-untyped-def]
    """Nodes+edges from ir_module_module_dependency (B.6, DB source).

    DB chosen over manifest parsing: it reflects installed reality for every
    module (including enterprise) without disk heuristics, and works whether
    the instance is running or not.
    """
    res = list_modules(instance, db_name, db_path)
    if not res.ok:
        return res
    sql = ("SELECT row_to_json(t) FROM (SELECT d.name AS module, "
           "dep.name AS depends_on FROM ir_module_module_dependency dep "
           "JOIN ir_module_module d ON d.id = dep.module_id "
           "ORDER BY 1, 2) t;")
    rc, out = _pg(instance, db_name, sql, timeout=120)
    if rc != 0:
        return Result.failure(f"Cannot read dependencies in '{db_name}': {out[:200]}")
    edges = []
    for line in out.splitlines():
        try:
            row = json.loads(line)
            edges.append([row.get("module", ""), row.get("depends_on", "")])
        except ValueError:
            continue
    states = {m.get("name"): m.get("state") for m in res.data["modules"]}
    names = sorted(set(states) | {e[0] for e in edges} | {e[1] for e in edges})
    nodes = [{"name": n, "state": states.get(n, "unknown")} for n in names if n]
    edges = [e for e in edges if e[0] and e[1]]
    return Result.success(
        data={"nodes": nodes, "edges": edges, "database": db_name},
        message=f"{len(nodes)} module(s), {len(edges)} dependenc(ies)")


def run_module_tests(instance, db_name: str, module_name: str,  # type: ignore[no-untyped-def]
                     progress_cb=None, cancel=None, db_path=None) -> Result:
    """Run one module's test suite on a NON-primary database (Sprint 11.3).

    Target selection (explicit, never silent): missing DB → `-i <module>
    --test-enable` (creates + installs + tests in one shot); existing DB →
    `-u <module> --test-enable` (faster, preserves). The primary database
    is REFUSED outright — tests create/modify/destroy data. Flags
    --test-enable/--stop-after-init are stable across 15.0–19.0.
    """
    from odoo_vite.core.db_state import get_db_state
    from odoo_vite.core.proc import run_streaming

    module = (module_name or "").strip()
    if not module:
        return Result.failure("Module name is required")
    target = (db_name or "").strip()
    if not target:
        return Result.failure("Target test database is required")
    if target == (instance.primary_db or "").strip():
        return Result.failure(
            f"Refusing to run tests on '{target}' — it is this instance's "
            "PRIMARY database. Pick (or type) a disposable test database; "
            "tests can create, modify and destroy data")
    venv_python = Path(effective_python(instance))
    odoo_bin = Path(instance.community_path) / "odoo-bin"
    if not venv_python.is_file():
        return Result.failure(f"Venv python missing at {venv_python}")
    if not odoo_bin.is_file():
        return Result.failure(f"odoo-bin missing at {odoo_bin}")
    if inst_conf_missing(instance):
        return Result.failure(f"odoo.conf missing at {instance.conf_path}")
    if _running_on(instance, target, db_path) is not None:
        return Result.failure(
            f"Instance is running on '{target}' — stop it first; test runs "
            "must own their database")
    state = get_db_state(target, instance.db_user or "odoo",
                         _password_for(instance) or None)
    if state.error and not state.exists:
        return Result.failure(f"Cannot inspect '{target}': {state.error}")
    if state.exists and state.initialized:
        mode, flag = "update-in-place (-u)", ["-u", module]
    else:
        mode, flag = ("create+install (-i)"
                      if not state.exists else "initialize+install (-i)", ["-i", module])
    cmd = [str(venv_python), str(odoo_bin), "-c", instance.conf_path,
           "-d", target, *flag, "--test-enable", "--stop-after-init"]
    res = run_streaming(cmd, progress_cb=progress_cb, cancel=cancel,
                        timeout=3600)
    summary = parse_test_summary((res.data or {}).get("lines", []))
    if not res.ok:
        return Result.failure(
            f"Test run failed to complete ({mode}): {res.message}",
            data={"summary": summary, "database": target, "module": module})
    return Result.success(
        data={"summary": summary, "database": target, "module": module,
              "mode": mode},
        message=(f"Tests {summary['status']} for '{module}' "
                  f"({summary['ran']} ran): {summary['text'][:160]}"))


def inst_conf_missing(instance) -> bool:  # type: ignore[no-untyped-def]
    return bool(instance.conf_path) and not Path(instance.conf_path).is_file()


def parse_test_summary(lines: list[str]) -> dict:
    """Parse unittest-style tail: 'Ran N tests' + 'OK' / 'FAILED (...)'."""
    import re as _re

    tail = "\n".join(lines[-30:] if lines else [])
    ran = 0
    match = _re.search(r"Ran (\d+) tests?", tail)
    if match:
        try:
            ran = int(match.group(1))
        except ValueError:
            ran = 0
    status, text = "unknown", ""
    if _re.search(r"^OK(\s|$)", tail, _re.MULTILINE):
        status, text = "passed", "OK"
    else:
        fail = _re.search(r"^FAILED( \(.*\))?", tail, _re.MULTILINE)
        if fail:
            status, text = "failed", f"FAILED{fail.group(1) or ''}".strip()
    return {"ran": ran, "status": status, "text": text or tail[-200:]}
