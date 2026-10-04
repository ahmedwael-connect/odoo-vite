"""GitHub integration (3.3.0 Feature B): token, git auth, install/sync/publish.

Token lives in the OS keyring (same pattern as odoo_rpc credentials) and is
handed to git ONLY through a GIT_ASKPASS helper + env vars — never argv,
never logs. SSH remotes are detected and left alone (E2: your SSH keys, not
our token). Public HTTPS repos work with no token at all.
"""

from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from odoo_vite.core.result import Result

KEYRING_SERVICE = "odoo-vite"
KEYRING_ACCOUNT = "github:token"
API_BASE = "https://api.github.com"
_HTTP_TIMEOUT = 30
_BRANCH_TTL = 600.0

# ------------------------------------------------------------------- token


def _keyring_set(token: str) -> None:
    import keyring

    keyring.set_password(KEYRING_SERVICE, KEYRING_ACCOUNT, token)


def _keyring_get() -> str:
    try:
        import keyring

        return keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT) or ""
    except Exception:
        return ""


def _keyring_del() -> None:
    try:
        import keyring

        keyring.delete_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
    except Exception:
        pass


def get_token() -> str:
    """Saved token or "" (never logged, never returned to the UI)."""
    return _keyring_get()


def token_status() -> dict:
    return {"saved": bool(_keyring_get())}


def validate_token(token: str) -> Result:
    """GET /user with the token → login. The only network check."""
    raw = (token or "").strip()
    if not raw:
        return Result.failure("No token given")
    req = urllib.request.Request(
        f"{API_BASE}/user",
        headers={
            "Authorization": f"Bearer {raw}",
            "User-Agent": "odoo-vite (github-token-check)",
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return Result.failure(
                "GitHub rejected the token (401/403) — check the token's "
                "scopes (needs 'repo' for private repositories)")
        return Result.failure(f"GitHub API error {exc.code} — try again")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return Result.failure(f"Cannot reach api.github.com: {exc}")
    login = str((data or {}).get("login") or "").strip()
    if not login:
        return Result.failure("Token validated but no login returned")
    return Result.success(
        data={"login": login}, message=f"Token valid for '{login}'")


def save_token(token: str) -> Result:
    """Validate → keyring. Returns the login; the token never round-trips."""
    res = validate_token(token)
    if not res.ok:
        return res
    try:
        _keyring_set(token.strip())
    except Exception as exc:
        return Result.failure(f"Cannot save to the OS keyring: {exc}")
    login = str((res.data or {}).get("login") or "")
    return Result.success(
        data={"login": login}, message=f"GitHub token saved for '{login}'")


def clear_token() -> Result:
    try:
        _keyring_del()
    except Exception as exc:
        return Result.failure(f"Cannot clear the keyring entry: {exc}")
    return Result.success(message="GitHub token cleared")


# ----------------------------------------------------------------- askpass

_ASKPASS_SCRIPT = """#!/bin/sh
case "$1" in
  *sername*) printf '%s\\n' 'x-access-token' ;;
  *) printf '%s\\n' "$ODOO_VITE_GH_TOKEN" ;;
esac
"""
_askpass_file: str | None = None


def _askpass_path() -> str:
    """One private temp helper per process (0700, removed at exit)."""
    global _askpass_file
    if _askpass_file is None:
        fd, path = tempfile.mkstemp(prefix="ov-askpass-")
        with os.fdopen(fd, "w") as fh:
            fh.write(_ASKPASS_SCRIPT)
        os.chmod(path, 0o700)
        _askpass_file = path

        def _cleanup(p: str = path) -> None:
            try:
                os.unlink(p)
            except OSError:
                pass

        atexit.register(_cleanup)
    return _askpass_file


def _is_ssh_url(url: str) -> bool:
    raw = (url or "").strip()
    return raw.startswith("git@") or raw.startswith("ssh://")


def git_auth_env(remote_url: str = "") -> dict[str, str]:
    """Env additions so git can authenticate over https with the saved token.

    E2: SSH remotes get NOTHING (your keys, not our token). Plain http://
    gets nothing either — the token is never handed to an unencrypted
    channel. https without a saved token gets nothing (public repos).
    """
    raw = (remote_url or "").strip()
    if not raw or _is_ssh_url(raw) or raw.startswith("http://"):
        return {}
    token = get_token()
    if not token:
        return {}
    return {
        "GIT_ASKPASS": _askpass_path(),
        "SSH_ASKPASS": _askpass_path(),
        "GIT_ASKPASS_REQUIRE": "force",
        "GIT_TERMINAL_PROMPT": "0",
        "ODOO_VITE_GH_TOKEN": token,
    }


# ---------------------------------------------------------------- helpers


def _repo_url(repo: str) -> Result:
    """owner/repo | full git URL | existing local path → git URL."""
    raw = (repo or "").strip()
    if not raw:
        return Result.failure("No repository given")
    if raw.startswith(("http://", "https://", "git@", "ssh://", "file://")):
        return Result.success(data=raw, message="")
    if Path(raw).expanduser().exists():
        return Result.success(data=str(Path(raw).expanduser()), message="")
    if re.fullmatch(r"[\w.-]+/[\w.-]+", raw):
        return Result.success(data=f"https://github.com/{raw}", message="")
    return Result.failure(
        f"Not a repository: {raw!r} — use owner/repo or a full git URL")


def _repo_name(repo: str) -> str:
    """Safe folder/API name from owner/repo or URL (odoo-safe chars)."""
    raw = (repo or "").strip().rstrip("/").removesuffix(".git")
    part = raw.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    name = re.sub(r"[^a-z0-9_]+", "_", part.lower()).strip("_")
    return name or "github_repo"


def _run_git(
    cwd: Path,
    args: list[str],
    *,
    auth_url: str = "",
    timeout: int = 120,
) -> Result:
    """One-shot git query (stdout+stderr as Result.data on success)."""
    git = shutil.which("git")
    if git is None:
        return Result.failure("git is not installed (required for GitHub operations)")
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_PAGER"] = "cat"
    env["PAGER"] = "cat"
    env.update(git_auth_env(auth_url))
    try:
        proc = subprocess.run(
            [git, "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return Result.failure(f"git {' '.join(args)} timed out after {timeout}s")
    except OSError as exc:
        return Result.failure(f"Cannot run git: {exc}")
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode != 0:
        first = out.splitlines()[0] if out else f"git {' '.join(args)} failed"
        return Result.failure(first)
    return Result.success(data=out, message="")


def _stream_git(
    cwd: Path,
    args: list[str],
    progress_cb: Callable[[str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    *,
    auth_url: str = "",
    timeout: int = 600,
) -> Result:
    """Streaming git (clone/push/add/commit) with auth env merged in."""
    from odoo_vite.core.proc import run_streaming

    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_PAGER"] = "cat"
    env["PAGER"] = "cat"
    env.update(git_auth_env(auth_url))
    return run_streaming(
        ["git", "-C", str(cwd), *args],
        progress_cb=progress_cb,
        cancel=cancel,
        timeout=timeout,
        env=env,
    )


def _same_path(a: str, b: str) -> bool:
    try:
        return Path(a).expanduser().resolve() == Path(b).expanduser().resolve()
    except OSError:
        return str(a) == str(b)


def _module_dirs(root: Path) -> list[Path]:
    """Module dirs at depth 0 (single module) or 1 (repo/addons layout)."""
    try:
        if (root / "__manifest__.py").is_file():
            return [root]
        children = [p for p in sorted(root.iterdir()) if p.is_dir()
                    and not p.name.startswith(".")]
        out = [p for p in children if (p / "__manifest__.py").is_file()]
        if out:
            return out
        for child in children:
            out.extend(
                p for p in sorted(child.iterdir())
                if p.is_dir() and not p.name.startswith(".")
                and (p / "__manifest__.py").is_file()
            )
        return out
    except OSError:
        return []


def _git_root(start: Path) -> Path | None:
    """Walk up from `start` to the nearest .git (bounded)."""
    cur = start if start.is_dir() else start.parent
    for _ in range(12):
        if (cur / ".git").exists():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def _origin_url(root: Path) -> str:
    res = _run_git(root, ["remote", "get-url", "origin"])
    return str(res.data or "").strip() if res.ok else ""


# ---------------------------------------------------------------- branches

_BRANCH_CACHE: dict[str, tuple[float, list[str]]] = {}


def clear_branch_cache() -> None:
    _BRANCH_CACHE.clear()


def _stale_branches(key: str, why: str) -> Result:
    hit = _BRANCH_CACHE.get(key)
    if hit:
        return Result.success(
            data=list(hit[1]),
            message=f"{len(hit[1])} branches (stale; refresh failed: {why})",
        )
    return Result.failure(why)


def list_repo_branches(repo: str, refresh: bool = False) -> Result:
    """git ls-remote --heads → branch names (600s cache, serve-stale)."""
    url_res = _repo_url(repo)
    if not url_res.ok:
        return url_res
    url = str(url_res.data)
    key = url.rstrip("/").removesuffix(".git")
    if not refresh and key in _BRANCH_CACHE:
        at_, branches = _BRANCH_CACHE[key]
        if (time.monotonic() - at_) < _BRANCH_TTL:
            return Result.success(
                data=list(branches), message=f"{len(branches)} branches (cached)")
    git = shutil.which("git")
    if git is None:
        return Result.failure("git is not installed (required for branch listing)")
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.update(git_auth_env(url))
    try:
        proc = subprocess.run(
            [git, "ls-remote", "--heads", url],
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return _stale_branches(key, "timed out")
    except OSError as exc:
        return Result.failure(f"Cannot run git: {exc}")
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "git ls-remote failed").strip()
        return _stale_branches(key, err.splitlines()[0])
    branches: list[str] = []
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].startswith("refs/heads/"):
            branches.append(parts[1][len("refs/heads/"):])
    branches = sorted(set(branches))
    _BRANCH_CACHE[key] = (time.monotonic(), branches)
    return Result.success(data=branches, message=f"{len(branches)} branches")


# ------------------------------------------------------------ remote create


def create_remote_repo(name: str, private: bool = True) -> Result:
    """POST /user/repos — empty repo under the token's account.

    "already exists" is a success (we'll push to it); everything else is a
    plain failure. Requires a saved token.
    """
    clean = re.fullmatch(r"[A-Za-z0-9._-]+", (name or "").strip() or "x")
    if not clean:
        return Result.failure(f"Invalid repository name: {name!r}")
    token = get_token()
    if not token:
        return Result.failure(
            "Creating a GitHub repository needs a saved token "
            "(Preferences → GitHub)")
    req = urllib.request.Request(
        f"{API_BASE}/user/repos",
        data=json.dumps(
            {"name": name, "private": bool(private), "auto_init": False}
        ).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "odoo-vite (github-create-repo)",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except OSError:
            pass
        if exc.code == 422 and "already_exists" in body:
            return Result.success(
                data={"full_name": name, "existed": True},
                message=f"Repository '{name}' already exists — will push to it")
        if exc.code in (401, 403):
            return Result.failure(
                "GitHub rejected the token (401/403) — needs 'repo' scope "
                "to create repositories")
        return Result.failure(f"GitHub API error {exc.code} while creating '{name}'")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return Result.failure(f"Cannot reach api.github.com: {exc}")
    full = str((data or {}).get("full_name") or name)
    return Result.success(
        data={"full_name": full, "existed": False},
        message=f"Created GitHub repository '{full}' (private)")


# ------------------------------------------------------------ module lookup

def _find_module_dir(inst, mod: str) -> Path | None:  # type: ignore[no-untyped-def]
    """Locate `<root>/<mod>` across the instance's addons paths.

    Order = Odoo's own resolution order (stored addons_state first), then
    the well-known fallbacks, so sync hits the same folder Odoo loads.
    """
    from odoo_vite.core import addon_paths

    roots: list[str] = []
    seen: set[str] = set()

    def _add(raw) -> None:
        text = str(raw or "").strip()
        if not text:
            return
        try:
            text = str(Path(text).expanduser())
        except (OSError, ValueError):
            return
        if text not in seen:
            seen.add(text)
            roots.append(text)

    for entry in addon_paths.get_addons_state(inst):
        if entry.get("path") and entry.get("enabled", True):
            _add(entry["path"])
    if inst.path:
        _add(Path(inst.path).expanduser() / "addons")
    _add(inst.custom_addons_path)
    if inst.community_path:
        _add(Path(inst.community_path).expanduser() / "addons")
        _add(Path(inst.community_path).expanduser() / "odoo" / "addons")
    _add(inst.enterprise_path)
    for root in roots:
        cand = Path(root) / mod
        if (cand / "__manifest__.py").is_file():
            return cand
    return None


def _custom_folder(inst) -> Path | None:  # type: ignore[no-untyped-def]
    """The instance's own code folder: wired custom addons if it exists,
    else the instance-local <path>/addons (created on demand)."""
    candidates = []
    raw = str(getattr(inst, "custom_addons_path", "") or "").strip()
    if raw:
        candidates.append(Path(raw).expanduser())
    if inst.path:
        candidates.append(Path(inst.path).expanduser() / "addons")
    for cand in candidates:
        try:
            if cand.is_dir():
                return cand
        except OSError:
            continue
    if inst.path:
        try:
            made = Path(inst.path).expanduser() / "addons"
            made.mkdir(parents=True, exist_ok=True)
            return made
        except OSError:
            return None
    return None


def publish_state(instance_id: str, db_path=None) -> dict:
    """What Publish would target (plain payload, never raises)."""
    from odoo_vite.core import registry

    empty = {"folder": "", "root": "", "in_git": False, "remote": "",
             "branch": "", "error": ""}
    inst = registry.get_instance(instance_id, db_path)
    if inst is None:
        return {**empty, "error": f"No instance with id '{instance_id}'"}
    folder = _custom_folder(inst)
    if folder is None:
        return {**empty, "error": "Instance has no folder on disk"}
    root = _git_root(folder)
    remote, branch = "", ""
    if root is not None:
        remote = _origin_url(root)
        # symbolic-ref (not rev-parse) — works on unborn branches too
        res = _run_git(root, ["symbolic-ref", "--short", "HEAD"])
        branch = str(res.data or "").strip() if res.ok else ""
    return {"folder": str(folder), "root": str(root or ""),
            "in_git": root is not None, "remote": remote, "branch": branch,
            "error": ""}


# ----------------------------------------------------------------- install


async def install_repo(
    instance_id: str,
    repo: str,
    branch: str,
    progress_cb: Callable[[str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    db_path=None,
) -> Result:
    """Clone owner/repo@branch into <instance>/addons + wire addons_path.

    Single-module repos land as the module folder itself; repo-style trees
    get their container registered as an addons entry. The -i step stays an
    explicit UI confirmation (same two-phase flow as the zip installer).
    """
    from odoo_vite.core import addon_paths, registry
    from odoo_vite.core.git_manager import clone_repo

    def _emit(line: str) -> None:
        if progress_cb is not None:
            try:
                progress_cb(line)
            except Exception:
                pass

    if cancel and cancel():
        return Result.failure("Cancelled")
    inst = registry.get_instance(instance_id, db_path)
    if inst is None:
        return Result.failure(f"No instance with id '{instance_id}'")
    if not inst.path:
        return Result.failure("Instance has no folder on disk")
    url_res = _repo_url(repo)
    if not url_res.ok:
        return url_res
    url = str(url_res.data)
    branch = (branch or "").strip()
    if not branch:
        return Result.failure("No branch selected")

    base = Path(inst.path).expanduser() / "addons"
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return Result.failure(f"Cannot create {base}: {exc}")
    name = _repo_name(repo)
    dest = base / name
    if dest.exists():
        return Result.failure(
            f"{dest} already exists — remove it, or use Sync/Pull to update")

    _emit(f"=== clone {repo} @ {branch} → {dest} ===")
    cloned = clone_repo(
        url, branch, dest,
        progress_cb=progress_cb, cancel=cancel,
        label=f"{repo} ({branch})",
        env=git_auth_env(url) or None,
    )
    if not cloned.ok:
        shutil.rmtree(dest, ignore_errors=True)
        return Result.failure(cloned.message)

    roots = _module_dirs(dest)
    if not roots:
        shutil.rmtree(dest, ignore_errors=True)
        return Result.failure(
            f"Cloned {name} but found no Odoo module (__manifest__.py) — "
            "removed the clone")

    entries = addon_paths.get_addons_state(inst)
    changed = False
    for parent in sorted({str(d.parent) for d in roots}):
        if not any(_same_path(str(e.get("path") or ""), parent)
                   for e in entries):
            entries.append({"path": parent, "enabled": True})
            changed = True
    if changed:
        applied = addon_paths.apply_addons_state(instance_id, entries, db_path)
        if not applied.ok:
            return Result.failure(
                f"Cloned to {dest} but the addons_path update failed: "
                f"{applied.message}")

    mods = [d.name for d in roots]
    return Result.success(
        data={"modules": mods, "path": str(dest)},
        message=f"Cloned {repo} ({branch}) — {', '.join(mods)} ready in "
                f"{inst.name}",
    )


# ------------------------------------------------------------------- sync


async def sync_module(
    instance_id: str,
    name: str,
    progress_cb: Callable[[str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    db_path=None,
) -> Result:
    """Fast-forward the git repo CONTAINING this module (Feature A pull)."""
    from odoo_vite.core import registry, updates

    mod = (name or "").strip()
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", mod):
        return Result.failure(f"Invalid module name: {mod!r}")
    inst = registry.get_instance(instance_id, db_path)
    if inst is None:
        return Result.failure(f"No instance with id '{instance_id}'")
    mdir = _find_module_dir(inst, mod)
    if mdir is None:
        return Result.failure(
            f"Module '{mod}' not found on {inst.name}'s addons paths")
    root = _git_root(mdir)
    if root is None:
        return Result.failure(
            f"{mdir} is not inside a git checkout — nothing to pull")
    res = updates.pull_path(str(root), progress_cb=progress_cb, cancel=cancel)
    if res.ok:
        return Result.success(
            data=res.data, message=f"'{mod}': {res.message}")
    return Result.failure(f"'{mod}': {res.message}", data=res.data)


# ---------------------------------------------------------------- publish

_SECRET_RE = re.compile(
    r"(^|/)(\.env(\..+)?|[^/]+\.pem|[^/]+\.key|[^/]+\.p12|[^/]+\.pfx|"
    r"id_(rsa|dsa|ecdsa|ed25519)|\.netrc)$",
    re.IGNORECASE,
)


def _looks_secret(path: str) -> bool:
    return bool(_SECRET_RE.search(path))


def _changed_files(porcelain: str) -> list[str]:
    out: list[str] = []
    for line in (porcelain or "").splitlines():
        if len(line) < 4:
            continue
        entry = line[3:].strip()
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1]
        out.append(entry.strip().strip('"'))
    return [f for f in out if f]


def _is_github_https(url: str) -> bool:
    return (url or "").startswith("https://github.com/")


async def publish_addons(
    instance_id: str,
    message: str,
    branch: str = "",
    remote: str = "",
    create: bool = False,
    progress_cb: Callable[[str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    db_path=None,
) -> Result:
    """Commit + push the instance's custom code. Secret files are refused.

    create=True initializes a git repo (and, for https://github.com
    remotes, the remote repository itself via the API — needs a token).
    """
    from odoo_vite.core import registry

    def _emit(line: str) -> None:
        if progress_cb is not None:
            try:
                progress_cb(line)
            except Exception:
                pass

    msg = (message or "").strip()
    if not msg:
        return Result.failure("A commit message is required")
    if cancel and cancel():
        return Result.failure("Cancelled")
    inst = registry.get_instance(instance_id, db_path)
    if inst is None:
        return Result.failure(f"No instance with id '{instance_id}'")
    folder = _custom_folder(inst)
    if folder is None:
        return Result.failure(
            "Instance has no addons folder to publish")

    root = _git_root(folder)
    auth_url = ""
    if root is None:
        if not create:
            return Result.failure(
                f"{folder} is not a git repository — give a remote URL and "
                "tick 'Create new repository'")
        want = _repo_url(remote)
        if not want.ok:
            return Result.failure(
                "A remote (owner/repo or full URL) is required to create "
                f"a repository — {want.message}")
        auth_url = str(want.data)
        _emit(f"=== git init in {folder} ===")
        res = _run_git(folder, ["init"])
        if not res.ok:
            return Result.failure(f"git init failed: {res.message}")
        res = _run_git(folder, ["remote", "add", "origin", auth_url])
        if not res.ok:
            return Result.failure(f"git remote add failed: {res.message}")
        target_branch = (branch or "").strip()
        if target_branch:
            res = _run_git(folder, ["checkout", "-B", target_branch])
            if not res.ok:
                return Result.failure(f"git checkout failed: {res.message}")
        if _is_github_https(auth_url):
            _emit(f"=== ensure GitHub repository {_repo_name(remote)} ===")
            created = create_remote_repo(_repo_name(remote))
            if created.ok:
                _emit(created.message)
            else:
                _emit(f"note: {created.message} — trying to push anyway")
        root = folder
    else:
        existing = _origin_url(root)
        want_raw = (remote or "").strip()
        if want_raw:
            want = _repo_url(want_raw)
            if not want.ok:
                return want
            auth_url = str(want.data)
            if existing and not _same_path(existing, auth_url):
                res = _run_git(root, ["remote", "set-url", "origin", auth_url])
            elif not existing:
                res = _run_git(root, ["remote", "add", "origin", auth_url])
            else:
                res = None
            if res is not None and not res.ok:
                return Result.failure(f"git remote update failed: {res.message}")
        elif not existing:
            return Result.failure(
                "This repository has no 'origin' remote — give a URL "
                "(or tick 'Create new repository')")
        else:
            auth_url = existing
        if create and _is_github_https(auth_url):
            created = create_remote_repo(_repo_name(auth_url))
            if created.ok:
                _emit(created.message)

    # secret scan BEFORE staging anything
    st = _run_git(root, ["status", "--porcelain"])
    if not st.ok:
        return Result.failure(f"Cannot read git status: {st.message}")
    files = _changed_files(str(st.data or ""))
    suspects = [f for f in files if _looks_secret(f)]
    if suspects:
        shown = ", ".join(suspects[:10]) + (" …" if len(suspects) > 10 else "")
        return Result.failure(
            "Refusing to publish — secret-looking files in the change set: "
            f"{shown}",
            data={"files": suspects},
        )

    if cancel and cancel():
        return Result.failure("Cancelled")
    _emit("=== git add -A ===")
    added = _stream_git(root, ["add", "-A"], progress_cb, cancel,
                        auth_url=auth_url)
    if not added.ok:
        return Result.failure(f"git add failed: {added.message}")

    _emit("=== git commit ===")
    committed = _stream_git(
        root, ["commit", "-m", msg], progress_cb, cancel, auth_url=auth_url)
    commit_note = ""
    if not committed.ok:
        output = "\n".join((committed.data or {}).get("lines", []))
        if "nothing to commit" in output or "no changes added" in output:
            commit_note = " (nothing new to commit)"
        else:
            return Result.failure(f"git commit failed: {committed.message}")

    if cancel and cancel():
        return Result.failure("Cancelled")
    _emit("=== git push -u origin HEAD ===")
    pushed = _stream_git(
        root, ["push", "-u", "origin", "HEAD"],
        progress_cb, cancel, auth_url=auth_url, timeout=1800)
    if not pushed.ok:
        reason = (
            "cancelled"
            if (pushed.data or {}).get("cancelled")
            else pushed.message
        )
        return Result.failure(f"git push failed: {reason}")

    head = _run_git(root, ["rev-parse", "--short", "HEAD"])
    rev = str(head.data or "?").strip() if head.ok else "?"
    return Result.success(
        data={"root": str(root), "remote": auth_url, "rev": rev},
        message=f"Pushed {rev} to {auth_url}{commit_note}",
    )
