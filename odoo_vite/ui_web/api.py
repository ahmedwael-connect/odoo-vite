"""API facade for the pywebview frontend (web cutover Phase 1).

One domain class per ops class in ``odoo_vite.ops`` (the framework-free
ops layer both frontends shared; formerly housed in ``ui_slint/``).

Translations applied at this boundary:

- **async -> sync**: ops methods are coroutines; pywebview runs every
  js_api call on a fresh worker thread (no event loop), so each method
  drives its coroutine with ``asyncio.run``. No bridge loop, no queue.
- **Result -> envelope**: returns become ``{"ok", "message", "data"}``.
- **sinks -> push events**: constructor sinks are wired to a
  :class:`PushChannel`; kind names match the old ``_drain`` taxonomy.
- **progress/cancel**: ops that stream get ``op_id``; lines push as
  ``progress-line`` and ``progress-done`` is emitted from a ``finally``
  so the browser can never get stuck busy (the sticky-flag bug class).
- **never raises**: every public method is wrapped — unexpected
  exceptions become ``{"ok": False, "message": "..."}``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import functools
import threading
from pathlib import Path
from typing import Any, Callable

from odoo_vite.core import process_manager, provisioning, registry
from odoo_vite.core import version as version_mod
from odoo_vite.core import addon_paths, audit, backup_scheduler, db_manager
from odoo_vite.core import venv_manager
from odoo_vite.core.enterprise import detect_enterprise
from odoo_vite.core.instance import Instance
from odoo_vite.core.log_tail import read_last_n
from odoo_vite.core.odoo_shell import OdooShell
from odoo_vite.ops.configuration import ConfigOps, read_conf_view, validate_meta
from odoo_vite.ops.databases import DatabaseOps, group_discover_entries
from odoo_vite.ops.devwatch import DevWatchOps
from odoo_vite.ops.devtools import (
    DevToolsOps,
    diff_record_values,
    editable_fields,
    format_cron_line,
    format_meta_line,
    format_record_label,
)
from odoo_vite.ops.lifecycle import LifecycleOps
from odoo_vite.ops.logs import (
    LogOps,
    format_doctor_lines,
    format_search_lines,
    format_slow_lines,
    parse_profile_duration,
)
from odoo_vite.ops.modules import (
    ModuleOps,
    preview_command,
    split_deps,
    state_category,
)
from odoo_vite.ops.transfer import (
    TransferOps,
    bundle_filename,
    read_preferences,
    save_preferences,
)
from odoo_vite.ops.wizards import (
    WizardOps,
    build_draft,
    build_adopt_overrides,
    build_scaffold_definition,
    gap_rows,
    generate_password,
    parse_adopt_paths,
    parse_field_lines,
    refresh_draft,
    suggest_db_name,
    validate_details,
    validate_locate,
    validate_scaffold,
)

from odoo_vite.ui_web.push import PushChannel
from odoo_vite.ui_web.serialize import to_payload

# --------------------------------------------------------------------- helpers


def _safe(fn: Callable) -> Callable:
    """Run fn, normalize the result, and convert raises into ok:false."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return to_payload(fn(*args, **kwargs))
        except Exception as exc:  # noqa: BLE001 — UI boundary never raises
            return {"ok": False, "message": f"{type(exc).__name__}: {exc}"}

    wrapper._web_safe = True  # type: ignore[attr-defined]
    return wrapper


class Domain:
    """Base for API domains: wraps every public method with ``_safe``."""

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        for name, attr in list(cls.__dict__.items()):
            if name.startswith("_") or not callable(attr):
                continue
            if getattr(attr, "_web_safe", False):
                continue
            setattr(cls, name, _safe(attr))


def _sink_message(push: PushChannel) -> Callable:
    def sink(text, level="info"):
        push.emit("message", {"text": str(text), "level": str(level or "info")})

    return sink


def _sink_refresh(push: PushChannel) -> Callable:
    def sink():
        push.emit("refresh", {})

    return sink


def _sink(push: PushChannel, kind: str, fields: tuple) -> Callable:
    def sink(*args):
        push.emit(kind, dict(zip(fields, args)))

    return sink


class CancelRegistry:
    """op_id -> threading.Event for cancellable streamed operations."""

    def __init__(self) -> None:
        self._events: dict[str, threading.Event] = {}

    def begin(self, op_id: str) -> threading.Event:
        event = threading.Event()
        self._events[op_id] = event
        return event

    def end(self, op_id: str) -> None:
        self._events.pop(op_id, None)

    def cancel(self, op_id: str) -> bool:
        event = self._events.get(op_id)
        if event is None:
            return False
        event.set()
        return True


def _progress_call(
    push: PushChannel,
    registry_: CancelRegistry,
    fn: Callable,
    op_id: str,
    *args,
):
    """Run an async ops method that accepts progress_cb/cancel kwargs.

    progress-done is emitted in ``finally`` — success, failure, or crash —
    so the browser's busy flag always clears.
    """
    event = registry_.begin(op_id) if op_id else None

    def progress_cb(line) -> None:
        push.emit("progress-line", {"op_id": op_id, "line": str(line)})

    try:
        value = asyncio.run(
            fn(
                *args,
                progress_cb=progress_cb,
                cancel=(event.is_set if event is not None else None),
            )
        )
        return to_payload(value)
    finally:
        if op_id:
            registry_.end(op_id)
        push.emit("progress-done", {"op_id": op_id})


_INSTANCE_FIELDS = {f.name for f in dataclasses.fields(Instance)}


def _instance_from_dict(payload: dict) -> Instance:
    if not isinstance(payload, dict):
        raise TypeError("draft must be an object")
    return Instance(**{k: v for k, v in payload.items() if k in _INSTANCE_FIELDS})


# ------------------------------------------------------------------------ App


class AppApi(Domain):
    """Registry/status reads, preferences, native pickers, version."""

    def __init__(self, push: PushChannel, file_dialog: Callable | None = None) -> None:
        self._push = push
        self._file_dialog = file_dialog

    def instances(self) -> list[dict]:
        return [dataclasses.asdict(i) for i in registry.list_instances()]

    def instance(self, instance_id: str) -> dict | None:
        inst = registry.get_instance(instance_id)
        return dataclasses.asdict(inst) if inst is not None else None

    def enterprise(self, instance_id: str) -> Any:
        inst = registry.get_instance(instance_id)
        if inst is None:
            return {"ok": False, "message": "Instance not found"}
        return detect_enterprise(inst)

    def statuses(self) -> list[dict]:
        return process_manager.get_statuses()

    def suggest_port(self, start: int = 8069) -> int:
        return provisioning.suggest_port(int(start))

    def preferences(self) -> dict:
        return read_preferences()

    def save_preferences(self, mode: str) -> Any:
        return save_preferences(mode)

    def pick_file(self, title: str = "", mode: str = "open", pattern: str = "") -> dict:
        """Native file dialog; mode is 'open' | 'save'. Window bootstraps this."""
        if self._file_dialog is None:
            return {"ok": False, "message": "file dialog not wired"}
        path = self._file_dialog(mode, title, pattern)
        return {"ok": True, "path": path}

    def pick_dir(self, title: str = "") -> dict:
        if self._file_dialog is None:
            return {"ok": False, "message": "file dialog not wired"}
        path = self._file_dialog("folder", title, "")
        return {"ok": True, "path": path}

    def version(self) -> str:
        return version_mod.__version__


# ------------------------------------------------------------------- Lifecycle


class LifecycleApi(Domain):
    def __init__(self, push: PushChannel) -> None:
        self._push = push
        self._ops = LifecycleOps(
            on_message=_sink_message(push), on_refresh=_sink_refresh(push)
        )

    def start(self, instance_id: str, database=None, confirm: bool = False) -> Any:
        # confirm=False -> core refuses with data.needs_confirm + preview;
        # React re-calls with confirm=True once the user has approved.
        confirm_cb = (lambda _preview: True) if confirm else None
        return asyncio.run(self._ops.start(instance_id, database, confirm_cb))

    def stop(self, instance_id: str) -> Any:
        return asyncio.run(self._ops.stop(instance_id))

    def restart(self, instance_id: str) -> Any:
        return asyncio.run(self._ops.restart(instance_id))

    def remove(
        self, instance_id: str, drop_db: bool = False, drop_extra_dbs=None
    ) -> Any:
        return asyncio.run(
            self._ops.remove(instance_id, bool(drop_db), drop_extra_dbs)
        )

    def clone(self, instance_id: str, new_name: str, new_port=None) -> Any:
        return asyncio.run(self._ops.clone(instance_id, new_name, new_port))


# ------------------------------------------------------------------- Databases


class DatabasesApi(Domain):
    def __init__(self, push: PushChannel) -> None:
        self._push = push
        self._ops = DatabaseOps(
            on_message=_sink_message(push),
            on_refresh=_sink_refresh(push),
            on_states=_sink(push, "db-states", ("instance_id", "states")),
            on_report=_sink(push, "db-report", ("instance_id", "report")),
            on_schedules=_sink(
                push, "db-schedules", ("instance_id", "schedules", "total")
            ),
            on_discover=_sink(push, "discover-ready", ("instance_id", "entries")),
        )

    def refresh_states(self, instance_id: str) -> Any:
        return asyncio.run(self._ops.refresh_states(instance_id))

    def server_reachable(self) -> bool:
        return bool(db_manager.server_reachable())

    def list_backup_files(self, instance_id: str) -> list[dict]:
        inst = registry.get_instance(instance_id)
        if inst is None:
            return []
        return backup_scheduler.list_backup_files(inst.name)

    def discover_entries(self, instance_id: str) -> Any:
        return asyncio.run(self._ops.discover_entries(instance_id))

    def init_db(self, instance_id: str, db_name: str) -> Any:
        return asyncio.run(self._ops.init_db(instance_id, db_name))

    def drop_db(self, instance_id: str, db_name: str) -> Any:
        return asyncio.run(self._ops.drop_db(instance_id, db_name))

    def backup_db(self, instance_id: str, db_name: str, dest: str) -> Any:
        return asyncio.run(self._ops.backup_db(instance_id, db_name, dest))

    def restore_db(self, instance_id: str, dump: str, target: str) -> Any:
        return asyncio.run(self._ops.restore_db(instance_id, dump, target))

    def validate(self, instance_id: str) -> Any:
        return asyncio.run(self._ops.validate(instance_id))

    def track(self, instance_id: str, db_name: str) -> Any:
        return asyncio.run(self._ops.track(instance_id, db_name))

    def untrack(self, instance_id: str, db_name: str) -> Any:
        return asyncio.run(self._ops.untrack(instance_id, db_name))

    def track_many(self, instance_id: str, db_names: list) -> Any:
        return asyncio.run(self._ops.track_many(instance_id, db_names))

    def set_primary(self, instance_id: str, db_name: str) -> Any:
        return asyncio.run(self._ops.set_primary(instance_id, db_name))

    def refresh_schedules(self, instance_id: str) -> Any:
        return asyncio.run(self._ops.refresh_schedules(instance_id))

    def run_schedule_now(self, schedule_id: str) -> Any:
        return asyncio.run(self._ops.run_schedule_now(schedule_id))

    def switch_db(
        self, instance_id: str, db_name: str, confirm: bool = False
    ) -> Any:
        confirm_cb = (lambda _preview: True) if confirm else None
        return asyncio.run(self._ops.switch_db(instance_id, db_name, confirm_cb))

    def sched_create(self, instance_id: str, payload: dict) -> Any:
        return asyncio.run(self._ops.sched_create(instance_id, payload))

    def sched_update(self, schedule_id: str, payload: dict) -> Any:
        return asyncio.run(self._ops.sched_update(schedule_id, payload))

    def sched_delete(self, schedule_id: str) -> Any:
        return asyncio.run(self._ops.sched_delete(schedule_id))

    def sched_toggle(self, schedule_id: str, enabled: bool) -> Any:
        return asyncio.run(self._ops.sched_toggle(schedule_id, bool(enabled)))

    def file_delete(self, dump_path: str) -> Any:
        return asyncio.run(self._ops.file_delete(dump_path))

    def group_entries(self, entries: list, instance_version: str) -> dict:
        return group_discover_entries(entries, instance_version)


# --------------------------------------------------------------------- Modules


class ModulesApi(Domain):
    def __init__(self, push: PushChannel, cancels: CancelRegistry) -> None:
        self._push = push
        self._cancels = cancels
        self._ops = ModuleOps(
            on_message=_sink_message(push),
            on_refresh=_sink_refresh(push),
            on_modules=_sink(
                push, "modules-ready", ("instance_id", "modules", "diff", "error")
            ),
            on_deps=_sink(
                push,
                "deps-ready",
                ("instance_id", "name", "depends", "required_by"),
            ),
        )

    def cancel(self, op_id: str) -> dict:
        if self._cancels.cancel(op_id):
            return {"ok": True, "message": "cancelling"}
        return {"ok": False, "message": f"no running operation '{op_id}'"}

    def refresh_modules(self, instance_id: str) -> Any:
        return asyncio.run(self._ops.refresh_modules(instance_id))

    def install(self, instance_id: str, names: list, op_id: str = "") -> Any:
        return _progress_call(
            self._push, self._cancels, self._ops.install, op_id, instance_id, names
        )

    def update(self, instance_id: str, names: list, op_id: str = "") -> Any:
        return _progress_call(
            self._push, self._cancels, self._ops.update, op_id, instance_id, names
        )

    def uninstall(self, instance_id: str, name: str, op_id: str = "") -> Any:
        return _progress_call(
            self._push, self._cancels, self._ops.uninstall, op_id, instance_id, name
        )

    def update_code(self, instance_id: str, op_id: str = "") -> Any:
        return _progress_call(
            self._push, self._cancels, self._ops.update_code, op_id, instance_id
        )

    def fetch_deps(self, instance_id: str, name: str) -> Any:
        return asyncio.run(self._ops.fetch_deps(instance_id, name))

    def run_tests(
        self, instance_id: str, db_name: str, module: str, op_id: str = ""
    ) -> Any:
        return _progress_call(
            self._push,
            self._cancels,
            self._ops.run_tests,
            op_id,
            instance_id,
            db_name,
            module,
        )

    # pure helpers (sync, no push)
    def state_category(self, mod: dict) -> str:
        return state_category(mod)

    def state_categories(self, modules: list) -> dict:
        """Batch state_category — one round trip for a whole module list."""
        return {
            str(m.get("name", "")): state_category(m)
            for m in (modules or [])
            if isinstance(m, dict)
        }

    def preview_command(self, instance_id: str, db_name: str, flag: str, names: list) -> str:
        inst = registry.get_instance(instance_id)
        if inst is None:
            raise ValueError(f"No instance with id '{instance_id}'")
        return preview_command(inst, db_name, flag, names)

    def split_deps(self, edges: list, name: str) -> list:
        depends, required_by = split_deps(edges, name)
        return [list(depends), list(required_by)]


# ----------------------------------------------------------------- Configuration


class ConfigApi(Domain):
    def __init__(self, push: PushChannel, cancels: CancelRegistry) -> None:
        self._push = push
        self._cancels = cancels
        self._ops = ConfigOps(
            on_message=_sink_message(push), on_refresh=_sink_refresh(push)
        )

    def read(self, instance_id: str) -> dict:
        inst = registry.get_instance(instance_id)
        if inst is None:
            return {"ok": False, "message": "Instance not found"}
        return read_conf_view(inst)

    def save(self, instance_id: str, changes: dict) -> Any:
        return asyncio.run(self._ops.save(instance_id, changes))

    def restore(self, instance_id: str) -> Any:
        return asyncio.run(self._ops.restore(instance_id))

    def regenerate(self, instance_id: str) -> Any:
        return asyncio.run(self._ops.regenerate(instance_id))

    def meta_save(self, instance_id: str, meta: dict) -> Any:
        return asyncio.run(self._ops.meta_save(instance_id, meta))

    def apply_addons(self, instance_id: str, entries: list) -> Any:
        return asyncio.run(self._ops.apply_addons(instance_id, entries))

    def validate_meta(self, meta: dict) -> str | None:
        return validate_meta(meta)

    def addons_state(self, instance_id: str) -> list[dict]:
        inst = registry.get_instance(instance_id)
        if inst is None:
            return []
        return addon_paths.get_addons_state(inst)

    def looks_like_addons(self, path: str) -> bool:
        return bool(addon_paths.looks_like_addons_folder(path))

    def venv_status(self, instance_id: str) -> dict:
        """Is the instance's venv python present? (Overview U5.2 warning.)"""
        inst = registry.get_instance(instance_id)
        if inst is None:
            return {"venv_path": "", "python_ok": False,
                    "error": "Instance not found"}
        venv = Path(inst.venv_path).expanduser() if inst.venv_path else None
        python = venv / "bin" / "python" if venv else None
        return {
            "venv_path": str(inst.venv_path or ""),
            "python_ok": bool(python and python.exists()),
        }

    def rebuild_venv(self, instance_id: str, op_id: str = "") -> Any:
        """Fresh venv + requirements + verify (U5.2; streams, cancelable)."""

        async def _rebuild(iid, progress_cb=None, cancel=None, db_path=None):
            return await asyncio.to_thread(
                venv_manager.rebuild_venv, iid,
                progress_cb=progress_cb, cancel=cancel, db_path=db_path,
            )

        return _progress_call(
            self._push, self._cancels, _rebuild, op_id, instance_id,
        )


# ------------------------------------------------------------------------ Logs


class LogsApi(Domain):
    def __init__(self, push: PushChannel) -> None:
        self._push = push
        self._ops = LogOps(
            on_message=_sink_message(push),
            on_search=_sink(
                push,
                "log-search",
                ("instance_id", "ok", "message", "matches"),
            ),
            on_doctor=_sink(push, "log-doctor", ("instance_id", "findings")),
            on_slow=_sink(
                push, "log-slow", ("instance_id", "ok", "message", "rows")
            ),
            on_profile=_sink(
                push, "profile-ready", ("instance_id", "ok", "message", "svg")
            ),
        )

    def tail(self, instance_id: str, n: int = 500) -> dict:
        """Sync tail of the instance log (the 1s poll replacement)."""
        inst = registry.get_instance(instance_id)
        if inst is None:
            return {"ok": False, "message": "Instance not found", "lines": []}
        if not inst.log_path:
            return {"ok": False, "message": "No log file recorded", "lines": []}
        return {"ok": True, "lines": read_last_n(inst.log_path, int(n))}

    def search(self, instance_id: str, pattern: str, level=None) -> Any:
        return asyncio.run(self._ops.search(instance_id, pattern, level))

    def doctor(self, instance_id: str) -> Any:
        return asyncio.run(self._ops.doctor(instance_id))

    def slow_refresh(self, instance_id: str) -> Any:
        return asyncio.run(self._ops.slow_refresh(instance_id))

    def profile(self, instance_id: str, duration: int = 10) -> Any:
        return asyncio.run(self._ops.profile(instance_id, int(duration)))

    # pure formatters
    def format_search(self, matches: list) -> list:
        return format_search_lines(matches)

    def format_doctor(self, findings: list) -> list:
        return format_doctor_lines(findings)

    def format_slow(self, rows: list) -> list:
        return format_slow_lines(rows)

    def parse_duration(self, text: str) -> int:
        return parse_profile_duration(text)


# --------------------------------------------------------------------- DevTools


class DevToolsApi(Domain):
    def __init__(self, push: PushChannel) -> None:
        self._push = push
        self._shell = OdooShell()
        self._ops = DevToolsOps(
            on_message=_sink_message(push),
            on_rpc=_sink(push, "dev-rpc", ("instance_id", "message")),
            on_models=_sink(push, "dev-models", ("instance_id", "models")),
            on_meta=_sink(push, "dev-meta", ("instance_id", "meta")),
            on_records=_sink(
                push, "dev-records", ("instance_id", "records", "offset", "more")
            ),
            on_crons=_sink(push, "dev-crons", ("instance_id", "crons")),
        )

    def rpc_connect(
        self, instance_id: str, user: str = "", password: str = "", remember: bool = False
    ) -> Any:
        return asyncio.run(
            self._ops.rpc_connect(instance_id, user, password, bool(remember))
        )

    def list_models(self, instance_id: str) -> Any:
        return asyncio.run(self._ops.list_models(instance_id))

    # ---------------------------------------------------------------- shell

    def shell_start(self, instance_id: str, db_name: str = "") -> Any:
        inst = registry.get_instance(instance_id)
        if inst is None:
            return {"ok": False, "message": "Instance not found"}
        return self._shell.start(inst, db_name or "")

    def shell_stop(self) -> Any:
        return self._shell.stop()

    def shell_send(self, line: str) -> Any:
        return self._shell.send_line(line)

    def shell_poll(self) -> dict:
        return {
            "running": bool(self._shell.running),
            "exit_code": self._shell.exit_code,
            "lines": self._shell.drain_output(),
        }

    def model_metadata(self, instance_id: str, model: str) -> Any:
        return asyncio.run(self._ops.model_metadata(instance_id, model))

    def last_meta(self, instance_id: str) -> dict:
        return self._ops.last_meta(instance_id)

    def cached_record(self, instance_id: str, record_id: int) -> Any:
        return self._ops.cached_record(instance_id, record_id)

    def records_page(self, instance_id: str, offset: int) -> Any:
        return asyncio.run(self._ops.records_page(instance_id, int(offset)))

    def rec_search(self, instance_id: str, field: str, op: str, value: str) -> Any:
        return asyncio.run(self._ops.rec_search(instance_id, field, op, value))

    def rec_page(self, instance_id: str, delta: int) -> Any:
        return asyncio.run(self._ops.rec_page(instance_id, int(delta)))

    def rec_create(self, instance_id: str, values: dict) -> Any:
        return asyncio.run(self._ops.rec_create(instance_id, values))

    def rec_update(self, instance_id: str, record_id: int, values: dict) -> Any:
        return asyncio.run(self._ops.rec_update(instance_id, int(record_id), values))

    def rec_delete(self, instance_id: str, record_id: int, expected: str) -> Any:
        return asyncio.run(self._ops.rec_delete(instance_id, int(record_id), expected))

    def cron_refresh(self, instance_id: str) -> Any:
        return asyncio.run(self._ops.cron_refresh(instance_id))

    def launch_json(self, instance_id: str) -> Any:
        return asyncio.run(self._ops.launch_json(instance_id))

    def open_editor(self, instance_id: str, editor: str) -> Any:
        return asyncio.run(self._ops.open_editor(instance_id, editor))

    # pure helpers
    def diff_record(self, current: dict, new: dict) -> dict:
        return diff_record_values(current, new)

    def editable_fields(self, meta, record) -> list:
        return editable_fields(meta, record)

    def format_meta_line(self, meta) -> str:
        return format_meta_line(meta)

    def format_record_label(self, record: dict) -> str:
        return format_record_label(record)

    def format_cron_line(self, cron: dict) -> str:
        return format_cron_line(cron)


# ---------------------------------------------------------------------- Wizards


class WizardsApi(Domain):
    def __init__(self, push: PushChannel, cancels: CancelRegistry) -> None:
        self._push = push
        self._cancels = cancels
        self._ops = WizardOps(
            on_message=_sink_message(push),
            on_branches=_sink(push, "wiz-branches", ("branches", "message")),
            on_syscheck=_sink(push, "wiz-syscheck", ("checks", "message")),
        )

    def cancel(self, op_id: str) -> dict:
        if self._cancels.cancel(op_id):
            return {"ok": True, "message": "cancelling"}
        return {"ok": False, "message": f"no running operation '{op_id}'"}

    def load_branches(self, search: str = "") -> Any:
        return asyncio.run(self._ops.load_branches(search))

    def run_syscheck(self, version: str) -> Any:
        return asyncio.run(self._ops.run_syscheck(version))

    def install_requirements(self, missing: list, op_id: str = "") -> Any:
        """pkexec/apt install of missing syscheck rows (streams, S3 parity).

        Runs blocking on this worker thread; every output line pushes as
        progress-line and progress-done fires in ``finally`` so the
        browser's busy flag can never stick.
        """
        from odoo_vite.core import system_check

        def on_line(line) -> None:
            self._push.emit("progress-line",
                            {"op_id": op_id, "line": str(line)})

        try:
            return system_check.install_requirements(
                [str(m) for m in (missing or [])], on_line=on_line)
        finally:
            if op_id:
                self._push.emit("progress-done", {"op_id": op_id})

    def provision(self, draft: dict, plaintext: bool = False, op_id: str = "") -> Any:
        inst = _instance_from_dict(draft)
        return _progress_call(
            self._push,
            self._cancels,
            self._ops.provision,
            op_id,
            inst,
            bool(plaintext),
        )

    def discard_draft(self, instance_id: str) -> Any:
        return asyncio.run(self._ops.discard_draft(instance_id))

    def adopt_run(
        self, name: str, conf: str, community: str, overrides: dict
    ) -> Any:
        return asyncio.run(self._ops.adopt_run(name, conf, community, overrides))

    def scaffold_install(
        self,
        definition: dict,
        dest: str,
        instance_id: str,
        db_name: str,
        op_id: str = "",
    ) -> Any:
        return _progress_call(
            self._push,
            self._cancels,
            self._ops.scaffold_install,
            op_id,
            definition,
            dest,
            instance_id,
            db_name,
        )

    # pure wizard helpers
    def generate_password(self, length: int = 20) -> str:
        return generate_password(int(length))

    def validate_details(self, values: dict) -> str | None:
        return validate_details(values)

    def build_draft(self, details: dict, version: str) -> Any:
        return build_draft(details, version)

    def refresh_draft(self, draft: dict, details: dict, version: str) -> Any:
        return refresh_draft(_instance_from_dict(draft), details, version)

    def parse_field_lines(self, text: str) -> list:
        return parse_field_lines(text)

    def build_scaffold_definition(self, values: dict) -> dict:
        return build_scaffold_definition(values)

    def validate_scaffold(self, values: dict, has_instances: bool) -> str | None:
        return validate_scaffold(values, bool(has_instances))

    def parse_adopt_paths(self, conf: str, community: str) -> dict:
        return parse_adopt_paths(conf, community)

    def validate_locate(self, name: str, conf: str, community: str) -> str | None:
        return validate_locate(name, conf, community)

    def gap_rows(self, parsed: dict, report: dict) -> list:
        return gap_rows(parsed, report)

    def suggest_db_name(self, name: str) -> str:
        return suggest_db_name(name)

    def build_adopt_overrides(
        self, parsed: dict, gap_values: dict, db_name: str = ""
    ) -> dict:
        return build_adopt_overrides(parsed, gap_values, db_name)


# --------------------------------------------------------------------- Transfer


class TransferApi(Domain):
    def __init__(self, push: PushChannel) -> None:
        self._push = push
        self._ops = TransferOps(
            on_message=_sink_message(push), on_refresh=_sink_refresh(push)
        )

    def preview(self, archive: str) -> Any:
        return self._ops.preview(archive)

    def export_bundle(self, instance_id: str, dest: str) -> Any:
        return asyncio.run(self._ops.export_bundle(instance_id, dest))

    def import_bundle(self, archive: str, new_name: str, new_port=None) -> Any:
        return asyncio.run(self._ops.import_bundle(archive, new_name, new_port))

    def bundle_filename(self, name: str, stamp: str) -> str:
        return bundle_filename(name, stamp)


# ------------------------------------------------------------------------ Audit


class AuditApi(Domain):
    """App-level audit event log (Sprint 8 ticket B.6 event dock).

    Reads ``~/.local/share/odoo-vite/audit.log`` — the JSONL stream
    core writes for lifecycle events (start/stop/db_create/…).
    """

    def tail(self, limit: int = 200) -> list[dict]:
        try:
            n = int(limit)
        except (TypeError, ValueError):
            n = 200
        return audit.read_events(max(1, min(n, 2000)))


# ------------------------------------------------------------------------ Watch


class WatchApi(Domain):
    """Dev Mode Watch (Sprint 11 ticket 11.2): toggle + status.

    The watcher runs in-process (watchdog inotify); events push as
    ``dev-watch``. Restart is injected so ops stays free of facade
    concerns — the same LifecycleOps sinks keep toasts/refresh flowing.
    """

    def __init__(self, push: PushChannel, restart_cb: Callable) -> None:
        self._ops = DevWatchOps(
            on_event=lambda payload: push.emit("dev-watch", payload),
            restart_cb=restart_cb,
        )

    def start(self, instance_id: str) -> Any:
        return self._ops.start(instance_id)

    def stop(self, instance_id: str) -> Any:
        return self._ops.stop(instance_id)

    def status(self, instance_id: str) -> dict:
        return self._ops.status(instance_id)

    def stop_all(self) -> None:
        """App-exit hygiene: never leave observer threads behind."""
        self._ops.stop_all()


# ------------------------------------------------------------------------- Api


class Api:
    """Root object handed to pywebview as ``js_api``.

    Exposed to JavaScript as::

        pywebview.api.app.instances()
        pywebview.api.lifecycle.start(id, null, true)
        pywebview.api.modules.install(id, names, opId)
    """

    def __init__(self, push: PushChannel, file_dialog: Callable | None = None) -> None:
        self._push = push
        cancels = CancelRegistry()
        self.app = AppApi(push, file_dialog)
        self.lifecycle = LifecycleApi(push)
        self.databases = DatabasesApi(push)
        self.modules = ModulesApi(push, cancels)
        self.config = ConfigApi(push, cancels)
        self.logs = LogsApi(push)
        self.devtools = DevToolsApi(push)
        self.wizards = WizardsApi(push, cancels)
        self.transfer = TransferApi(push)
        self.audit = AuditApi()
        life = LifecycleOps(
            on_message=_sink_message(push), on_refresh=_sink_refresh(push)
        )
        self.watch = WatchApi(push, life.restart)


def create_api(
    transport: Callable[[dict], None] | None = None,
    file_dialog: Callable | None = None,
) -> tuple[Api, PushChannel]:
    """Convenience builder: (api, push) with a transport wired in."""
    push = PushChannel(transport)
    return Api(push, file_dialog), push
