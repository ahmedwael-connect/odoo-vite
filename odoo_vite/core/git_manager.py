"""Odoo repo branch operations + community clone.

- list_odoo_branches(search="") -> Result (Sprint 1: live branch picker).
- clone_instance(version, dest_path, ...) -> Result (Sprint 2: shallow clone
  of the community repo into <dest_path>/community with live log streaming).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from odoo_vite.core.proc import run_streaming
from odoo_vite.core.result import Result

ODOO_REPO_URL = "https://github.com/odoo/odoo"

# User-facing releases only: "17.0", "saas-17.4", ... Internal CI branches
# such as "tmp.20.0" / "staging.18.0" are excluded from the version picker.
_VERSION_RE = re.compile(r"^(saas-)?\d+\.\d+$")

_CACHE_TTL_SECONDS = 600  # 10 minutes


@dataclass
class _BranchCache:
    branches: list[str] | None = None
    fetched_at: float = 0.0


_CACHE = _BranchCache()


def clear_cache() -> None:
    """Drop the in-memory branch cache (tests / manual refresh)."""
    _CACHE.branches = None
    _CACHE.fetched_at = 0.0


def _cache_valid() -> bool:
    return (
        _CACHE.branches is not None
        and (time.monotonic() - _CACHE.fetched_at) < _CACHE_TTL_SECONDS
    )


def _run_ls_remote() -> Result:
    git = shutil.which("git")
    if git is None:
        return Result.failure("git is not installed (required for branch listing)")
    try:
        proc = subprocess.run(
            [git, "ls-remote", "--heads", ODOO_REPO_URL],
            capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        return Result.failure("Timed out contacting github.com (network slow/unreachable)")
    except OSError as exc:
        return Result.failure(f"Cannot run git: {exc}")
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "unknown error").strip().splitlines()
        return Result.failure(f"git ls-remote failed: {err[0] if err else 'exit 1'}")
    branches: list[str] = []
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) != 2 or not parts[1].startswith("refs/heads/"):
            continue
        name = parts[1][len("refs/heads/"):]
        if _VERSION_RE.match(name):
            branches.append(name)
    # De-duplicate, newest versions first (numeric sort on X.Y, saas-* inline).
    def _sort_key(name: str) -> tuple:
        nums = [int(n) for n in re.findall(r"\d+", name)]
        return (nums + [0, 0])[:2]

    branches = sorted(set(branches), key=_sort_key, reverse=True)
    return Result.success(data=branches, message=f"Found {len(branches)} version branches")


def _filter(branches: list[str], search: str) -> list[str]:
    needle = search.strip().lower()
    if not needle:
        return list(branches)
    return [b for b in branches if needle in b.lower()]


def list_odoo_branches(search: str = "", refresh: bool = False) -> Result:
    """List Odoo version branches, fuzzy-filtered by `search`.

    Results are cached in-memory for the session; pass refresh=True to
    force a network round-trip.
    """
    if not refresh and _cache_valid():
        assert _CACHE.branches is not None
        filtered = _filter(_CACHE.branches, search)
        return Result.success(
            data=filtered,
            message=f"{len(filtered)} branches (cached)",
        )
    res = _run_ls_remote()
    if not res.ok:
        # Serve stale cache (if any) rather than leaving the UI empty.
        if _CACHE.branches is not None:
            filtered = _filter(_CACHE.branches, search)
            return Result.success(
                data=filtered,
                message=f"{len(filtered)} branches (stale cache; refresh failed: {res.message})",
            )
        return res
    _CACHE.branches = list(res.data or [])
    _CACHE.fetched_at = time.monotonic()
    filtered = _filter(_CACHE.branches, search)
    return Result.success(
        data=filtered,
        message=f"{len(filtered)} of {len(_CACHE.branches)} branches",
    )


# ---------------------------------------------------------------------------
# Sprint 2: community clone

def clone_instance(
    version: str,
    dest_path: str | Path,
    progress_cb: Callable[[str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> Result:
    """Shallow-clone the Odoo community repo for `version`.

    Clones `--branch <version> --depth 1` into `<dest_path>/community`,
    streaming git output line-by-line to progress_cb. Cancellable via
    `cancel` (the git child is terminated, not orphaned).
    """
    if shutil.which("git") is None:
        return Result.failure("git is not installed (required for cloning)")
    dest = Path(dest_path).expanduser()
    community = dest / "community"
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return Result.failure(f"Cannot create instance folder {dest}: {exc}")
    if community.exists() and any(community.iterdir()):
        return Result.failure(
            f"Destination {community} already exists and is not empty "
            "(resume logic lives in provisioning; refusing to overwrite)"
        )
    cmd = [
        "git", "clone", "--branch", version, "--depth", "1",
        ODOO_REPO_URL, str(community),
    ]
    res = run_streaming(cmd, progress_cb=progress_cb, cancel=cancel, timeout=1800)
    if not res.ok:
        return Result.failure(
            f"git clone of Odoo {version} failed: {res.message}",
            data={"community_path": str(community), **(res.data or {})},
        )
    return Result.success(
        data={"community_path": str(community)},
        message=f"Odoo {version} cloned to {community}",
    )
