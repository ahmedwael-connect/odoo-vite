"""App-wide 'Pull Odoo Updates' (3.3.0 Feature A).

Lists every unique community/enterprise checkout the registry knows about
and fast-forwards each to its upstream: fetch + ff-only merge, nothing
else. Dirty trees and diverged branches are reported and skipped — never a
stash, never a rebase, never a reset (plan: dirty = skip + report).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from odoo_vite.core.proc import run_streaming
from odoo_vite.core.result import Result

_FETCH_TIMEOUT = 600  # seconds for fetch / ff-merge per checkout


def collect_checkouts(db_path: Path | str | None = None) -> list[dict]:
    """Unique checkouts across ALL instances: {path, kind, instances}.

    community_path and enterprise_path from every registry row, deduped by
    expanded path (two instances sharing one source tree = one entry).
    """
    from odoo_vite.core import registry

    seen: dict[str, dict] = {}
    for inst in registry.list_instances(db_path):
        for kind, raw in (
            ("community", inst.community_path),
            ("enterprise", inst.enterprise_path),
        ):
            p = str(raw or "").strip()
            if not p:
                continue
            try:
                key = str(Path(p).expanduser())
            except (OSError, ValueError):
                key = p
            slot = seen.get(key)
            if slot is None:
                seen[key] = {"path": key, "kind": kind, "instances": [inst.name]}
            elif inst.name not in slot["instances"]:
                slot["instances"].append(inst.name)
    return [seen[k] for k in sorted(seen)]


def _origin_url(dest: Path) -> str:
    res = _git_out(dest, ["remote", "get-url", "origin"])
    return str(res.data or "").strip() if res.ok else ""


def _git_env(dest: Path | None = None) -> dict[str, str]:
    """Non-interactive git: fail fast on credential prompts instead of
    sitting on a hidden askpass for the full fetch timeout.

    With `dest`, the saved GitHub token is merged in for https remotes
    (Feature B — SSH remotes keep their own keys, see github.git_auth_env).
    """
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_PAGER"] = "cat"
    env["PAGER"] = "cat"
    if dest is not None:
        try:
            from odoo_vite.core import github

            env.update(github.git_auth_env(_origin_url(dest)))
        except Exception:
            pass
    return env


def _git_out(dest: Path, args: list[str], timeout: int = 120) -> Result:
    """One-shot git query — stdout (trimmed) as Result.data."""
    git = shutil.which("git")
    if git is None:
        return Result.failure("git is not installed (required for pulling updates)")
    try:
        proc = subprocess.run(
            [git, "-C", str(dest), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_git_env(),
        )
    except subprocess.TimeoutExpired:
        return Result.failure(f"git {' '.join(args)} timed out after {timeout}s")
    except OSError as exc:
        return Result.failure(f"Cannot run git: {exc}")
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "exit 1").strip().splitlines()
        return Result.failure(err[0] if err else "git failed")
    return Result.success(data=proc.stdout, message="")


def pull_path(
    path: str | Path,
    progress_cb: Callable[[str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> Result:
    """Fast-forward ONE checkout to its upstream (current branch).

    Preflight (.git present, clean tree) → fetch → ahead/behind counts →
    ff-only merge. Dirty/diverged trees fail with a report — the pull never
    touches local changes.
    """

    def _emit(line: str) -> None:
        if progress_cb is not None:
            try:
                progress_cb(line)
            except Exception:
                pass

    raw = str(path or "").strip()
    if not raw:
        return Result.failure("No path given")
    dest = Path(raw).expanduser()
    label = dest.name or raw
    if not dest.exists():
        return Result.failure(
            f"{label}: path does not exist — {dest}", data={"path": raw}
        )
    if not (dest / ".git").exists():
        return Result.failure(
            f"{label}: not a git checkout (adopted/non-git sources are "
            "updated by hand)",
            data={"path": raw},
        )
    if cancel and cancel():
        return Result.failure("Cancelled", data={"path": raw})

    _emit(f"=== {label} ({dest}) ===")

    st = _git_out(dest, ["status", "--porcelain"])
    if not st.ok:
        return Result.failure(
            f"{label}: cannot inspect working tree — {st.message}",
            data={"path": raw},
        )
    dirty = [ln for ln in str(st.data or "").splitlines() if ln.strip()]
    if dirty:
        return Result.failure(
            f"{label}: working tree has {len(dirty)} uncommitted change(s) — "
            "commit or stash them first; pull skipped",
            data={"path": raw, "dirty": len(dirty)},
        )

    if cancel and cancel():
        return Result.failure("Cancelled", data={"path": raw})

    _emit("--- fetch origin")
    fetched = run_streaming(
        ["git", "-C", str(dest), "fetch", "--prune", "origin"],
        progress_cb=_emit,
        cancel=cancel,
        timeout=_FETCH_TIMEOUT,
        env=_git_env(dest),
    )
    if not fetched.ok:
        reason = (
            "cancelled"
            if (fetched.data or {}).get("cancelled")
            else fetched.message
        )
        return Result.failure(
            f"{label}: fetch failed: {reason}", data={"path": raw}
        )

    up = _git_out(
        dest, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]
    )
    if not up.ok or not str(up.data or "").strip():
        return Result.failure(
            f"{label}: no upstream branch configured — run "
            "'git branch --set-upstream-to origin/<branch>' by hand",
            data={"path": raw},
        )
    upstream = str(up.data).strip()

    behind_res = _git_out(dest, ["rev-list", "--count", "HEAD..@{u}"])
    ahead_res = _git_out(dest, ["rev-list", "--count", "@{u}..HEAD"])
    if not behind_res.ok or not ahead_res.ok:
        return Result.failure(
            f"{label}: cannot compare with {upstream}", data={"path": raw}
        )
    try:
        behind = int(str(behind_res.data).strip())
        ahead = int(str(ahead_res.data).strip())
    except ValueError:
        return Result.failure(
            f"{label}: unexpected git output while counting commits",
            data={"path": raw},
        )

    head_res = _git_out(dest, ["rev-parse", "--short", "HEAD"])
    head = str(head_res.data or "?").strip() if head_res.ok else "?"

    if ahead and behind:
        return Result.failure(
            f"{label}: diverged from {upstream} (ahead {ahead} / behind "
            f"{behind}) — resolve by hand (rebase/merge); pull skipped",
            data={"path": raw, "ahead": ahead, "behind": behind},
        )
    if behind == 0:
        note = f" (local ahead by {ahead})" if ahead else ""
        return Result.success(
            data={"path": raw, "updated": 0, "head": head, "upstream": upstream},
            message=f"{label}: already up to date at {head}{note}",
        )

    if cancel and cancel():
        return Result.failure("Cancelled", data={"path": raw})
    _emit(f"--- fast-forward {behind} commit(s) to {upstream}")
    merged = run_streaming(
        ["git", "-C", str(dest), "merge", "--ff-only", upstream],
        progress_cb=_emit,
        cancel=cancel,
        timeout=_FETCH_TIMEOUT,
        env=_git_env(dest),
    )
    if not merged.ok:
        reason = (
            "cancelled"
            if (merged.data or {}).get("cancelled")
            else merged.message
        )
        return Result.failure(
            f"{label}: fast-forward failed: {reason}", data={"path": raw}
        )

    new_head = _git_out(dest, ["rev-parse", "--short", "HEAD"])
    head = str(new_head.data or "?").strip() if new_head.ok else head
    return Result.success(
        data={"path": raw, "updated": behind, "head": head, "upstream": upstream},
        message=f"{label}: updated {behind} commit(s) → {head}",
    )


async def pull_all(
    paths: list[str],
    progress_cb: Callable[[str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    db_path: Path | str | None = None,  # noqa: ARG001 — API symmetry
) -> Result:
    """Pull every selected checkout; one failure doesn't stop the rest.

    Aggregate ok only if all succeeded (incl. already-up-to-date);
    data.results carries per-path {path, status, message} for the report.
    """
    selected = [p for p in (str(x or "").strip() for x in (paths or [])) if p]
    if not selected:
        return Result.failure("No checkouts selected")

    results: list[dict] = []
    cancelled = False
    for raw in selected:
        if cancel and cancel():
            cancelled = True
            results.append(
                {"path": raw, "status": "cancelled", "message": "cancelled"}
            )
            break
        res = pull_path(raw, progress_cb=progress_cb, cancel=cancel)
        if res.ok:
            status = "ok"
        elif res.message.startswith("Cancelled"):
            status = "cancelled"
            cancelled = True
        else:
            status = "failed"
        results.append(
            {
                "path": raw,
                "status": status,
                "message": res.message,
                **{k: v for k, v in (res.data or {}).items() if k != "path"},
            }
        )

    data = {"results": results, "total": len(selected)}
    ok_n = sum(1 for r in results if r["status"] == "ok")
    updated_n = sum(int(r.get("updated") or 0) for r in results)
    fails = [r for r in results if r["status"] == "failed"]
    if cancelled:
        return Result.failure(
            f"Cancelled — {ok_n} of {len(selected)} checkout(s) updated",
            data=data,
        )
    if fails:
        more = f" (+{len(fails) - 1} more)" if len(fails) > 1 else ""
        return Result.failure(
            f"{len(fails)} of {len(selected)} checkout(s) failed — "
            f"{fails[0]['message']}{more}",
            data=data,
        )
    return Result.success(
        data=data,
        message=f"All {ok_n} checkout(s) up to date ({updated_n} commit(s) pulled)",
    )
