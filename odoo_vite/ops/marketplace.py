"""Marketplace operations (MP-1): async drivers for the UI facade.

Mirrors the ModuleOps shape (silent reads for the view, toasts for
user-initiated mutations):

- search/detail/stats/categories → silent (the view paints errors into
  a Banner + offline note; no toast noise on background refresh).
- index → toast + streamed progress (git clone/fetch lines).
- add_review/delete_review/sync_featured/record_download → toast.
- install_from_zip → toast + refresh (modules view must re-list).

All network/parse/storage lives in core.marketplace; this layer only
moves work off the caller thread and routes messages.
"""

from __future__ import annotations

import asyncio
import webbrowser
from urllib.parse import urlparse

from odoo_vite.core import marketplace
from odoo_vite.core.result import Result


def _open_url(url: str) -> Result:
    """Open an http(s) URL in the system browser (reCAPTCHA downloads)."""
    target = (url or "").strip()
    if urlparse(target).scheme.lower() not in ("http", "https"):
        return Result.failure("Refusing to open a non-http(s) URL")
    try:
        launched = webbrowser.open(target)
    except Exception as exc:  # noqa: BLE001 — no browser / no display
        return Result.failure(f"Cannot open the browser: {exc}")
    if launched is False:
        return Result.failure("No browser available to open the URL")
    return Result.success(message="Opened in your browser")


class MarketplaceOps:
    def __init__(self, on_message=None, on_refresh=None) -> None:
        self._message = on_message or (lambda _m, _k="info": None)
        self._refresh = on_refresh or (lambda: None)

    async def _run(self, fn, *args, **kwargs):
        res = await asyncio.to_thread(fn, *args, **kwargs)
        if isinstance(res, Result):
            self._message(res.message, "info" if res.ok else "error")
        self._refresh()
        return res

    # ------------------------------------------------------------------ reads

    async def search(self, query: str = "", order: str = "Relevance",
                     category: str = "", series: str = "",
                     price: str = "", author: str = "",
                     page: int = 1) -> Result:
        return await asyncio.to_thread(
            marketplace.search, query, order, category, series, price,
            author, page)

    async def detail(self, module_id: str) -> Result:
        return await asyncio.to_thread(marketplace.detail, module_id)

    async def stats(self) -> Result:
        return await asyncio.to_thread(marketplace.stats)

    async def categories(self) -> Result:
        return await asyncio.to_thread(marketplace.categories)

    # -------------------------------------------------------------- mutations

    async def index(self, owner_repo: str, branch: str = "",
                    progress_cb=None, cancel=None) -> Result:
        return await self._run(
            marketplace.index, owner_repo, branch,
            progress_cb=progress_cb, cancel=cancel)

    async def add_review(self, module_id: str, rating: int, title: str,
                         body: str, author: str) -> Result:
        return await self._run(
            marketplace.add_review, module_id, rating, title, body, author)

    async def delete_review(self, review_id: int) -> Result:
        return await self._run(marketplace.delete_review, review_id)

    async def sync_featured(self) -> Result:
        return await self._run(marketplace.sync_featured)

    async def install_from_zip(self, zip_path: str, instance_id: str,
                               module_id: str = "") -> Result:
        res = await self._run(
            marketplace.import_addon_zip, zip_path, instance_id)
        if res.ok and module_id:
            # marketplace_installs row(s) feed the Installed badges + stats
            try:
                row = await asyncio.to_thread(marketplace.get_module,
                                              module_id)
                version = str((row or {}).get("series") or "")
                for tech in (res.data or {}).get("modules", []):
                    await asyncio.to_thread(
                        marketplace.record_install, module_id, instance_id,
                        tech, version, "zip")
            except Exception:  # noqa: BLE001 — badges are best-effort
                pass
            self._refresh()
        return res

    async def watch_download(self, module_id: str, tech: str,
                             timeout: float = 600.0,
                             progress_cb=None, cancel=None) -> Result:
        res = await asyncio.to_thread(
            marketplace.watch_download, tech, timeout, progress_cb, cancel)
        if res.ok:
            marketplace.record_download(module_id)
        return res

    async def open_url(self, url: str) -> Result:
        return await asyncio.to_thread(_open_url, url)

    async def installed_in(self, instance_id: str) -> list[dict]:
        return await asyncio.to_thread(marketplace.installed_in,
                                       instance_id)

    def record_download(self, module_id: str) -> dict:
        """Fire-and-forget local download counter (sync by design)."""
        marketplace.record_download(module_id)
        return {"ok": True, "message": "counted"}
