"""apps.odoo.com marketplace mirror + GitHub module index (MP-1).

Read side mirrors the public Odoo Apps Store (server-rendered HTML — the
site has no official API; URLs + selectors pinned against fixtures in
tests/fixtures/marketplace/):

  - search/browse: GET /apps/modules/browse[?search=&order=&series=
    &price=&author=] (+ category path, /page/N)
  - detail:        GET /apps/modules/<series>/<tech>
  - charts:        GET /   (Top Apps / New Apps / Most Downloaded)

Everything fetched is TTL-cached in the registry SQLite
(marketplace_cache) so search/detail/stats serve stale data offline
(git_manager serve-stale pattern).

Install side is ASSISTED: store downloads are reCAPTCHA-gated
(POST /loempia/verify-download with grc3_token), so a plain HTTP client
cannot fetch zips. The UI opens the module page in a real browser/
webview, the user clicks the store's Download button, and
import_addon_zip() takes the resulting zip into an instance's addons
folder + conf. GitHub-indexed modules install straight from the local
clone (no gate).

GitHub side: index(owner/repo) shallow-clones the repo and upserts rows
with source='github' (manifest + README parsed locally).

Single network seam: _http_get_url(url) -> bytes (tests monkeypatch it;
core tests never touch the network).
"""

from __future__ import annotations

import ast
import html as html_lib
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from html.parser import HTMLParser
from pathlib import Path

from odoo_vite.core.result import Result
from odoo_vite.core import version as version_mod

# --------------------------------------------------------------------- mirror

APPS_BASE_URL_ENV = "ODOO_VITE_APPS_BASE_URL"
DEFAULT_BASE_URL = "https://apps.odoo.com"
PAGE_SIZE = 20
ORDERS = (
    "Relevance", "Best Sellers", "Name", "Ratings", "Lowest Price",
    "Highest Price", "Downloads", "Purchases", "Newest",
)
PRICES = ("Free", "Paid")

_TTL_SEARCH = 600.0        # 10 min
_TTL_DETAIL = 86400.0      # 24 h
_TTL_CHARTS = 43200.0      # 12 h
_TTL_STATS = 43200.0       # 12 h
_HTTP_TIMEOUT = 30

MAX_ZIP_BYTES = 512 * 1024 * 1024
MAX_ZIP_ENTRIES = 50000
MAX_ZIP_UNCOMPRESSED = 2048 * 1024 * 1024


def base_url() -> str:
    return (os.environ.get(APPS_BASE_URL_ENV) or DEFAULT_BASE_URL).rstrip("/")


def github_base() -> str:
    return (os.environ.get("ODOO_VITE_GITHUB_BASE")
            or "https://github.com").rstrip("/")


def cache_dir() -> Path:
    override = os.environ.get("ODOO_VITE_MARKETPLACE_CACHE")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".local" / "share" / "odoo-vite" / "marketplace"


def _http_get_url(url: str) -> bytes:
    """The one place that touches the network. Tests replace this."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"odoo-vite/{version_mod.__version__} (marketplace)",
            "Accept": "text/html,application/xhtml+xml,*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
        return resp.read()


def _cache_connect(db_path=None):
    from odoo_vite.core.registry import _connect as reg_connect

    conn = reg_connect(db_path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS marketplace_cache (
            key TEXT PRIMARY KEY,
            fetched_at REAL NOT NULL,
            body TEXT NOT NULL
        );
        """
    )
    return conn


def _cache_read(key: str, db_path=None) -> tuple[str, float] | None:
    try:
        with _cache_connect(db_path) as conn:
            row = conn.execute(
                "SELECT body, fetched_at FROM marketplace_cache WHERE key = ?",
                (key,),
            ).fetchone()
            return (row["body"], row["fetched_at"]) if row else None
    except Exception:
        return None


def _cache_write(key: str, body: str, db_path=None) -> None:
    try:
        with _cache_connect(db_path) as conn:
            conn.execute(
                "INSERT INTO marketplace_cache (key, fetched_at, body) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET "
                "fetched_at = excluded.fetched_at, body = excluded.body",
                (key, time.time(), body),
            )
            conn.commit()
    except Exception:
        pass  # a cache miss is never fatal


def http_get(path: str, ttl: float = _TTL_SEARCH, db_path=None) -> Result:
    """GET a store URL with TTL cache + stale-on-error (serve cache)."""
    url = path if path.startswith(("http://", "https://", "file://")) \
        else base_url() + "/" + path.lstrip("/")
    cached = _cache_read(url, db_path)
    now = time.time()
    if cached is not None and (now - cached[1]) < ttl:
        return Result.success(data=cached[0], message="cached")
    try:
        raw = _http_get_url(url)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        if cached is not None:
            return Result.success(
                data=cached[0],
                message=f"offline — showing cached copy ({exc})",
            )
        return Result.failure(f"Cannot reach the Odoo Apps Store: {exc}")
    body = raw.decode("utf-8", "replace")
    _cache_write(url, body, db_path)
    return Result.success(data=body, message="fetched")


# ------------------------------------------------------------------ parsing


def _strip_tags(fragment: str) -> str:
    return html_lib.unescape(re.sub(r"<[^>]+>", "", fragment or "")).strip()


def _normalize_img(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("//"):
        return "https:" + url
    return url


def parse_cards(page_html: str) -> list[dict]:
    """List/search/home cards → dicts (selectors pinned to fixtures)."""
    starts = [m.start() for m in re.finditer(
        r'<div class="loempia_app_entry loempia_app_card', page_html)]
    ends = [m.start() for m in re.finditer(
        r'<div class="loempia_app_entry loempia_app_card|<nav|<footer|</main',
        page_html)]
    items = []
    for idx, start in enumerate(starts):
        stop = next((e for e in ends if e > start), len(page_html))
        if idx + 1 < len(starts):
            stop = min(stop, starts[idx + 1])
        block = page_html[start:stop]
        href = re.search(r'href="/apps/modules/([^"/]+)/([^"?#]+)"', block)
        if not href:
            continue
        series, tech = href.group(1), href.group(2)
        summary = re.search(r'<p class="loempia_panel_summary">(.*?)</p>',
                            block, re.S)
        title = re.search(r'<h5[^>]*>\s*<b>(.*?)</b>', block, re.S)
        author = re.search(r'loempia_panel_author">\s*<b>(.*?)</b>', block, re.S)
        price_raw = re.search(r'loempia_panel_price[^>]*>\s*<b>(.*?)</b>',
                              block, re.S)
        votes = re.search(r'loempia_rating_stars" title="(\d+) votes?"', block)
        active = 0
        stars_m = re.search(r'loempia_rating_stars.*?<b>', block, re.S)
        if stars_m:
            active = stars_m.group(0).count("rating_star_active")
        purchases = re.search(r"Total Purchases: (\d+)", block)
        cover = re.search(r"background-image:\s*url\(([^)]+)\)", block)
        price = ""
        if price_raw:
            text = _strip_tags(price_raw.group(1)).replace("\xa0", " ")
            price = "FREE" if "FREE" in text.upper() else re.sub(r"\s+", "", text)
        items.append({
            "id": f"{series}/{tech}",
            "series": series,
            "tech": tech,
            "title": _strip_tags(title.group(1) if title else tech),
            "summary": _strip_tags(summary.group(1) if summary else ""),
            "author": _strip_tags(author.group(1) if author else ""),
            "price": price,
            "free": price.upper().startswith("FREE"),
            "rating_count": int(votes.group(1)) if votes else 0,
            "rating_stars": active,
            "purchases": int(purchases.group(1)) if purchases else 0,
            "cover_url": _normalize_img(cover.group(1)) if cover else "",
            "source": "mirror",
            "detail_url": f"/apps/modules/{series}/{tech}",
        })
    return items


def parse_total(page_html: str) -> int:
    m = re.search(r"(\d[\d,\.]*)\s*\n?\s*Apps found", page_html)
    if not m:
        m = re.search(r"(\d[\d,\.]*)\s+Apps found", page_html)
    if not m:
        return 0
    try:
        return int(re.sub(r"[^\d]", "", m.group(1)))
    except ValueError:
        return 0


def parse_categories(page_html: str) -> list[str]:
    names = re.findall(r'href="/apps/modules/category/([^"]+)/browse"',
                       page_html)
    seen: list[str] = []
    for n in names:
        name = urllib.parse.unquote(n)
        if name not in seen:
            seen.append(name)
    return seen


def _capture_div(page_html: str, start_regex: str) -> str:
    """Inner HTML of the first <div> matching start_regex (div-depth walk)."""
    m = re.search(start_regex, page_html)
    if not m:
        return ""
    i = m.start()
    depth = 0
    pos = i
    while pos < len(page_html):
        j = page_html.find("<", pos)
        if j < 0:
            return ""
        if page_html.startswith("<div", j) and (
                j + 4 >= len(page_html)
                or page_html[j + 4] in " \t\r\n>/"):
            depth += 1
            pos = j + 4
            continue
        if page_html.startswith("</div", j):
            depth -= 1
            if depth == 0:
                return page_html[m.end():j] if m.end() <= j else ""
            pos = j + 5
            continue
        pos = j + 1
    return ""


def parse_reviews(page_html: str) -> list[dict]:
    pane = _capture_div(page_html, r'<div[^>]*\bid="comments"[^>]*>')
    if not pane:
        return []
    parts = re.split(r'<div class="row[^"]*"\s+data-id="', pane)
    reviews = []
    for part in parts[1:]:
        subject = re.search(
            r'discussion_scroll_title">\s*<b>(.*?)</b>', part, re.S)
        author = re.search(r'itemprop="name">([^<]*)</span>', part)
        date = re.search(r'text-muted">on\s*<span>([^<]*)</span>', part)
        body_html = _capture_div(
            part,
            r'<div[^>]*class="[^"]*discussion_scroll_post[^"]*"[^>]*>')
        body_text = _strip_tags(body_html or "")
        if not (subject or body_text):
            continue
        reviews.append({
            "title": _strip_tags(subject.group(1)) if subject else "",
            "author": html_lib.unescape(author.group(1)) if author else "",
            "date": html_lib.unescape(date.group(1)) if date else "",
            "body": body_text,
            "rating": 0,
            "source": "store",
        })
    ids = re.findall(r'<div class="row[^"]*"\s+data-id="(\d+)"', pane)
    for review, rid in zip(reviews, ids):
        review["remote_id"] = rid
    return reviews


def sanitize_html(fragment: str) -> str:
    """Whitelist sanitizer for store-supplied description HTML.

    Allows a semantic subset; drops style/class/id and all event
    handlers; hrefs limited to http(s)/mailto; images to https (and
    protocol-relative, upgraded to https). script/style/iframe content
    is removed entirely.
    """
    allowed = {
        "p", "br", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li",
        "a", "img", "pre", "code", "strong", "b", "em", "i", "u", "s",
        "small", "sub", "sup", "blockquote", "hr", "table", "thead",
        "tbody", "tfoot", "tr", "th", "td", "div", "span", "dl", "dt",
        "dd", "figure", "figcaption", "address", "abbr", "cite", "q",
    }
    drop_with_content = {"script", "style", "iframe", "object", "embed",
                         "noscript", "template"}
    void = {"br", "hr", "img"}
    keep_attrs = {
        "a": ("href", "title"),
        "img": ("src", "alt", "width", "height", "loading"),
        "td": ("colspan", "rowspan"),
        "th": ("colspan", "rowspan"),
        "ol": ("start",),
        "q": ("cite",),
    }
    out: list[str] = []
    skip_stack: list[str] = []

    class _Sani(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if skip_stack:
                if tag in drop_with_content:
                    skip_stack.append(tag)
                return
            if tag in drop_with_content:
                skip_stack.append(tag)
                return
            if tag not in allowed:
                return
            kept = []
            for name, value in attrs:
                if name.lower() not in keep_attrs.get(tag, ()):
                    continue
                value = value or ""
                if name in ("href", "cite"):
                    v = value.strip()
                    if v.startswith("//"):
                        v = "https:" + v
                    if not (v.startswith("https://")
                            or v.startswith("http://")
                            or v.startswith("mailto:")):
                        continue
                    value = v
                elif name == "src":
                    v = value.strip()
                    if v.startswith("//"):
                        v = "https:" + v
                    if not v.startswith("https://"):
                        continue
                    value = v
                kept.append(f'{name}="{html_lib.escape(value, quote=True)}"')
            attrs_str = (" " + " ".join(kept)) if kept else ""
            if tag in void:
                out.append(f"<{tag}{attrs_str}>")
            else:
                out.append(f"<{tag}{attrs_str}>")

        def handle_startendtag(self, tag, attrs):
            self.handle_starttag(tag, attrs)

        def handle_endtag(self, tag):
            if skip_stack:
                if tag == skip_stack[-1]:
                    skip_stack.pop()
                return
            if tag in allowed and tag not in void:
                out.append(f"</{tag}>")

        def handle_data(self, data):
            if not skip_stack:
                out.append(html_lib.escape(data, quote=False))

        def handle_entityref(self, name):
            if not skip_stack:
                out.append(f"&{name};")

        def handle_charref(self, name):
            if not skip_stack:
                out.append(f"&#{name};")

    parser = _Sani(convert_charrefs=True)
    parser.feed(fragment or "")
    parser.close()
    return "".join(out)


def parse_detail(page_html: str, series: str) -> dict:
    """Detail page → module dict (selectors pinned to fixtures)."""
    def meta(prop: str) -> str:
        m = re.search(
            rf'itemprop="{prop}"[^>]*content="([^"]*)"', page_html)
        if m:
            return m.group(1)
        m = re.search(
            rf'content="([^"]*)"[^>]*itemprop="{prop}"', page_html)
        return m.group(1) if m else ""

    title = re.search(r'<h1[^>]*itemprop="name"[^>]*>\s*<b>(.*?)</b>',
                      page_html, re.S)
    author = re.search(
        r'itemprop="author".*?itemprop="name">([^<]*)<', page_html, re.S)
    tech = re.search(
        r'<b>Technical Name</b></td>\s*<td><code>\s*([^<]+?)\s*</code>',
        page_html)
    lic = re.search(r'<td><b>License</b></td><td>([^<]*)</td>', page_html)
    description = re.search(r'<meta name="description" content="([^"]*)"',
                            page_html) or re.search(
        r'<meta property="og:description" content="([^"]*)"', page_html)
    summary = description.group(1) if description else ""

    deps: list[dict] = []
    dep_m = re.search(r'<b>Odoo Apps Dependencies</b>', page_html)
    if dep_m:
        cell = re.search(r'</td>\s*<td>(.*?)</td>', page_html[dep_m.end():],
                         re.S)
        if cell:
            for label in re.findall(r"<span>(.*?)</span>", cell.group(1),
                                    re.S):
                label = _strip_tags(label)
                if not label:
                    continue
                tm = re.search(r"\(([^()]+)\)", label)
                deps.append({
                    "label": re.sub(r"\s*\([^()]+\)", "", label).strip(),
                    "tech": tm.group(1).strip() if tm else label,
                })

    versions: list[str] = []
    ver_m = re.search(r'<b[^>]*>\s*Versions\s*</b>', page_html)
    if ver_m:
        window = page_html[ver_m.end():ver_m.end() + 3000]
        versions = list(dict.fromkeys(re.findall(
            r'class="badge[^"]*">\s*([\d.]+)\s*</span>', window)))

    votes = re.search(r'loempia_rating_stars" title="(\d+) votes?"',
                      page_html)
    rating_value = meta("ratingValue")
    review_count = meta("reviewCount")
    rating_count = meta("ratingCount")
    if not rating_count and votes:
        rating_count = votes.group(1)

    downloads = re.search(
        r'title="Downloads"\s*><i class="fa fa-download"></i>\s*([\d.,]+)',
        page_html)
    purchases = re.search(
        r'title="Purchases"\s*><i class="fa fa-shopping-cart"></i>\s*([\d.,]+)',
        page_html)

    free = 'id="download_form"' in page_html
    dl_hash = re.search(r'name="dl_hash"\s+value="([^"]+)"', page_html)
    dl_version = re.search(r'js_apps_download[^>]*data-version="([^"]+)"',
                           page_html)

    def num(raw: str | None) -> int:
        if not raw:
            return 0
        try:
            return int(re.sub(r"[^\d]", "", raw))
        except ValueError:
            return 0

    availability: dict[str, bool] = {}
    avail_m = re.search(r'<b>\s*Availability\s*</b>', page_html)
    if avail_m:
        window = page_html[avail_m.end():avail_m.end() + 1500]
        for label, key in (("Odoo Online", "odoo_online"),
                           ("Odoo.sh", "odoosh"),
                           ("On Premise", "on_premise")):
            pos = window.find(label)
            if pos < 0:
                availability[key] = False
                continue
            icons = re.findall(r"fa fa-(check|times)",
                               window[max(0, pos - 250):pos])
            availability[key] = bool(icons and icons[-1] == "check")

    screenshots = []
    for img in re.findall(r"<img[^>]*>", page_html):
        if "screenshot" not in img:
            continue
        src = re.search(r'(?:data-)?src="([^"]+)"', img)
        if src:
            src_norm = _normalize_img(src.group(1))
            if src_norm.startswith("https://"):
                screenshots.append(src_norm)

    desc_html = sanitize_html(
        _capture_div(page_html, r'<div[^>]*\bid="desc"[^>]*>'))

    return {
        "series": series,
        "tech": tech.group(1) if tech else "",
        "id": f"{series}/{tech.group(1)}" if tech else "",
        "title": _strip_tags(title.group(1)) if title else "",
        "author": html_lib.unescape(author.group(1)) if author else "",
        "summary": html_lib.unescape(summary),
        "license": _strip_tags(lic.group(1)) if lic else "",
        "depends": deps,
        "versions": versions,
        "rating_value": float(rating_value) if rating_value else 0.0,
        "rating_count": num(rating_count),
        "review_count": num(review_count),
        "downloads": num(downloads.group(1) if downloads else ""),
        "purchases": num(purchases.group(1) if purchases else ""),
        "free": free,
        "dl_hash": dl_hash.group(1) if dl_hash else "",
        "dl_version": dl_version.group(1) if dl_version else series,
        "available": availability,
        "screenshots": screenshots,
        "cover_url": screenshots[0] if screenshots else "",
        "description_html": desc_html,
        "reviews": parse_reviews(page_html),
        "source": "mirror",
        "detail_url": f"/apps/modules/{series}/"
                      f"{tech.group(1) if tech else ''}",
    }


def parse_charts(page_html: str) -> dict[str, list[str]]:
    """Home Top/New/Downloaded sections → {section: ['series/tech', …]}."""
    marks = [(m.start(), m.group(1)) for m in re.finditer(
        r'<span class="fw-light">([^<]+)</span>', page_html)]
    out: dict[str, list[str]] = {}
    for idx, (start, name) in enumerate(marks):
        stop = marks[idx + 1][0] if idx + 1 < len(marks) else len(page_html)
        ids = [f"{m.group(1)}/{m.group(2)}" for m in re.finditer(
            r'href="/apps/modules/([^"/]+)/([^"?#]+)"', page_html[start:stop])]
        out[name] = list(dict.fromkeys(ids))
    return out


# ------------------------------------------------------------------ storage

MARKETPLACE_SCHEMA = """
CREATE TABLE IF NOT EXISTS marketplace_modules (
    id TEXT PRIMARY KEY,
    tech_name TEXT NOT NULL DEFAULT '',
    series TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL DEFAULT '',
    price TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    cover_url TEXT NOT NULL DEFAULT '',
    detail_url TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'mirror',
    official INTEGER NOT NULL DEFAULT 0,
    featured INTEGER NOT NULL DEFAULT 0,
    rating_value REAL NOT NULL DEFAULT 0,
    rating_count INTEGER NOT NULL DEFAULT 0,
    review_count INTEGER NOT NULL DEFAULT 0,
    purchases INTEGER NOT NULL DEFAULT 0,
    downloads INTEGER NOT NULL DEFAULT 0,
    versions_json TEXT NOT NULL DEFAULT '[]',
    depends_json TEXT NOT NULL DEFAULT '[]',
    license TEXT NOT NULL DEFAULT '',
    dl_hash TEXT NOT NULL DEFAULT '',
    free INTEGER NOT NULL DEFAULT 0,
    repo_url TEXT NOT NULL DEFAULT '',
    description_html TEXT NOT NULL DEFAULT '',
    manifest_json TEXT NOT NULL DEFAULT '',
    synced_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS marketplace_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    module_id TEXT NOT NULL,
    rating INTEGER NOT NULL DEFAULT 0,
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS marketplace_installs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    module_id TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    tech_name TEXT NOT NULL DEFAULT '',
    version TEXT NOT NULL DEFAULT '',
    method TEXT NOT NULL DEFAULT 'zip',
    installed_at TEXT NOT NULL DEFAULT ''
);
"""

_MODULE_FIELDS = (
    "id", "tech_name", "series", "title", "summary", "author", "price",
    "category", "cover_url", "detail_url", "source", "official",
    "featured", "rating_value", "rating_count", "review_count",
    "purchases", "downloads", "versions_json", "depends_json", "license",
    "dl_hash", "free", "repo_url", "description_html", "manifest_json",
    "synced_at",
)


def _connect(db_path=None):
    from odoo_vite.core.registry import _connect as reg_connect

    conn = reg_connect(db_path)
    conn.executescript(MARKETPLACE_SCHEMA)
    return conn


def upsert_modules(rows: list[dict], db_path=None) -> Result:
    if not rows:
        return Result.success(data={"count": 0}, message="No rows to store")
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    import json as _json

    try:
        with _connect(db_path) as conn:
            for row in rows:
                if not row.get("id"):
                    continue
                # partial update: only columns actually present in the
                # incoming row are written (a bare search result never
                # wipes detail data fetched earlier)
                present = [c for c in _MODULE_FIELDS
                           if c != "id" and c in row]
                values = {"id": row["id"], "synced_at": now}
                for c in present:
                    v = row[c]
                    if c in ("official", "free", "featured"):
                        v = int(bool(v))
                    elif c in ("versions_json", "depends_json",
                               "manifest_json") and not isinstance(v, str):
                        v = _json.dumps(v)
                    elif c == "rating_value":
                        v = float(v or 0)
                    elif isinstance(v, (list, dict)):
                        v = _json.dumps(v)
                    values[c] = v
                cols = ", ".join(values)
                ph = ", ".join("?" for _ in values)
                updates = ", ".join(
                    f"{c} = excluded.{c}"
                    for c in values if c != "id")
                conn.execute(
                    f"INSERT INTO marketplace_modules ({cols}) "
                    f"VALUES ({ph}) ON CONFLICT(id) DO UPDATE SET {updates}",
                    tuple(values.values()),
                )
            conn.commit()
        stored = sum(1 for r in rows if r.get("id"))
        return Result.success(data={"count": stored},
                              message=f"Stored {stored} module(s)")
    except Exception as exc:
        return Result.failure(f"Cannot store modules: {exc}")


def _row_to_module(row) -> dict:
    import json as _json
    d = dict(row)
    for key, col in (("versions", "versions_json"),
                     ("depends", "depends_json"),
                     ("manifest", "manifest_json")):
        try:
            d[key] = _json.loads(d.pop(col) or "[]")
        except Exception:
            d[key] = [] if key != "manifest" else {}
    d["official"] = bool(d.get("official"))
    d["featured"] = bool(d.get("featured"))
    d["free"] = bool(d.get("free"))
    d["rating_value"] = float(d.get("rating_value") or 0)
    d["tech"] = d.get("tech_name", "")  # mirror the remote card shape
    return d


def search_local(query: str = "", source: str = "", limit: int = 100,
                 db_path=None) -> list[dict]:
    """Cached/local rows (offline fallback + GitHub index results)."""
    sql = "SELECT * FROM marketplace_modules"
    where, params = [], []
    if source:
        where.append("source = ?")
        params.append(source)
    if query:
        needle = f"%{query.strip().lower()}%"
        where.append("(lower(title) LIKE ? OR lower(tech_name) LIKE ? "
                     "OR lower(summary) LIKE ? OR lower(author) LIKE ?)")
        params += [needle] * 4
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY lower(title) LIMIT ?"
    params.append(int(limit))
    try:
        with _connect(db_path) as conn:
            return [_row_to_module(r) for r in conn.execute(sql, params)]
    except Exception:
        return []


def get_module(module_id: str, db_path=None) -> dict | None:
    try:
        with _connect(db_path) as conn:
            row = conn.execute(
                "SELECT * FROM marketplace_modules WHERE id = ?",
                (module_id,),
            ).fetchone()
            return _row_to_module(row) if row else None
    except Exception:
        return None


def set_flags(module_id: str, *, official=None, featured=None,
              category=None, db_path=None) -> Result:
    sets, params = [], []
    for col, val in (("official", official), ("featured", featured),
                     ("category", category)):
        if val is not None:
            sets.append(f"{col} = ?")
            params.append(int(val) if isinstance(val, bool) else val)
    if not sets:
        return Result.failure("No flags to set")
    params.append(module_id)
    try:
        with _connect(db_path) as conn:
            cur = conn.execute(
                f"UPDATE marketplace_modules SET {', '.join(sets)} "
                "WHERE id = ?", params)
            conn.commit()
            if cur.rowcount == 0:
                return Result.failure(f"No module '{module_id}'")
        return Result.success(data={"id": module_id}, message="Flags updated")
    except Exception as exc:
        return Result.failure(f"Cannot update flags: {exc}")


def _like(query: str) -> str:
    return f"%{query.strip().lower()}%"


def db_stats(db_path=None) -> dict:
    out = {"cached": 0, "github": 0, "installs": 0, "reviews": 0,
           "avg_local_rating": 0.0, "categories": [], "featured": 0,
           "official": 0}
    try:
        with _connect(db_path) as conn:
            out["cached"] = conn.execute(
                "SELECT COUNT(*) c FROM marketplace_modules "
                "WHERE source='mirror'").fetchone()["c"]
            out["github"] = conn.execute(
                "SELECT COUNT(*) c FROM marketplace_modules "
                "WHERE source='github'").fetchone()["c"]
            out["installs"] = conn.execute(
                "SELECT COUNT(*) c FROM marketplace_installs"
            ).fetchone()["c"]
            row = conn.execute(
                "SELECT COUNT(*) c, AVG(rating) a FROM marketplace_reviews"
            ).fetchone()
            out["reviews"] = row["c"]
            out["avg_local_rating"] = round(row["a"] or 0, 2)
            out["featured"] = conn.execute(
                "SELECT COUNT(*) c FROM marketplace_modules "
                "WHERE featured=1").fetchone()["c"]
            out["official"] = conn.execute(
                "SELECT COUNT(*) c FROM marketplace_modules "
                "WHERE official=1").fetchone()["c"]
            out["categories"] = [
                r["category"] for r in conn.execute(
                    "SELECT DISTINCT category FROM marketplace_modules "
                    "WHERE category != '' ORDER BY lower(category)")
            ]
    except Exception:
        pass
    return out


# ------------------------------------------------------------------- reviews


def list_reviews(module_id: str, db_path=None) -> list[dict]:
    try:
        with _connect(db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM marketplace_reviews WHERE module_id = ? "
                "ORDER BY created_at DESC, id DESC", (module_id,))
            out = [dict(r) for r in rows]
            for row in out:
                row["source"] = "local"
            return out
    except Exception:
        return []


def add_review(module_id: str, rating: int, title: str = "",
               body: str = "", author: str = "", db_path=None) -> Result:
    try:
        rating = int(rating)
    except (TypeError, ValueError):
        return Result.failure("Rating must be a number 1–5")
    if not 1 <= rating <= 5:
        return Result.failure("Rating must be between 1 and 5")
    title = (title or "").strip()
    body = (body or "").strip()
    author = (author or "").strip()
    if not title and not body:
        return Result.failure("Write a title or a review body first")
    if len(title) > 200 or len(body) > 4000:
        return Result.failure("Review too long (title ≤ 200, body ≤ 4000)")
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        with _connect(db_path) as conn:
            conn.execute(
                "INSERT INTO marketplace_reviews "
                "(module_id, rating, title, body, author, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (module_id, rating, title, body, author, now))
            conn.commit()
        return Result.success(data={"module_id": module_id},
                              message="Review saved locally")
    except Exception as exc:
        return Result.failure(f"Cannot save review: {exc}")


def delete_review(review_id: int, db_path=None) -> Result:
    try:
        with _connect(db_path) as conn:
            cur = conn.execute(
                "DELETE FROM marketplace_reviews WHERE id = ?",
                (int(review_id),))
            conn.commit()
            if cur.rowcount == 0:
                return Result.failure("No such review")
        return Result.success(message="Review deleted")
    except Exception as exc:
        return Result.failure(f"Cannot delete review: {exc}")


# ------------------------------------------------------------------- installs


def record_install(module_id: str, instance_id: str, tech: str,
                   version: str = "", method: str = "zip",
                   db_path=None) -> Result:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        with _connect(db_path) as conn:
            conn.execute(
                "INSERT INTO marketplace_installs "
                "(module_id, instance_id, tech_name, version, method, "
                "installed_at) VALUES (?, ?, ?, ?, ?, ?)",
                (module_id, instance_id, tech, version, method, now))
            conn.commit()
        return Result.success(message="Install recorded")
    except Exception as exc:
        return Result.failure(f"Cannot record install: {exc}")


def installed_in(instance_id: str, db_path=None) -> list[dict]:
    try:
        with _connect(db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM marketplace_installs WHERE instance_id = ? "
                "ORDER BY installed_at DESC", (instance_id,))
            return [dict(r) for r in rows]
    except Exception:
        return []


def record_download(module_id: str, db_path=None) -> None:
    """Count a store download attempt (best-effort local counter)."""
    try:
        with _connect(db_path) as conn:
            row = conn.execute(
                "SELECT downloads FROM marketplace_modules WHERE id = ?",
                (module_id,)).fetchone()
            if row is None:
                return
            conn.execute(
                "UPDATE marketplace_modules SET downloads = ? WHERE id = ?",
                (int(row["downloads"] or 0) + 1, module_id))
            conn.commit()
    except Exception:
        pass


DOWNLOADS_DIR_ENV = "ODOO_VITE_DOWNLOADS_DIR"
_WATCH_POLL_S = 1.0
_WATCH_HEARTBEAT_S = 15.0


def downloads_dir() -> Path:
    override = os.environ.get(DOWNLOADS_DIR_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / "Downloads"


def watch_download(pattern: str, timeout: float = 600.0,
                   progress_cb=None, cancel=None, db_path=None) -> Result:
    """Poll the downloads folder for a finished ``*<pattern>*.zip``.

    A file counts as finished once its size is > 0 and unchanged across
    two consecutive polls — that ignores ``.part``/``.crdownload`` files
    still in flight. Accepts zips that were already there before the
    watch started (user downloaded it manually).
    """
    pat = (pattern or "").strip().lower()
    if not pat:
        return Result.failure("Nothing to look for (empty download pattern)")
    folder = downloads_dir()
    if progress_cb:
        progress_cb(f"Watching {folder} for *{pat}*.zip …")
    last_sizes: dict[str, tuple[int, int]] = {}
    deadline = time.time() + max(1.0, float(timeout))
    last_beat = time.time()
    while time.time() < deadline:
        if cancel is not None and cancel():
            return Result.failure("Cancelled")
        sizes_now: dict[str, tuple[int, int]] = {}
        try:
            candidates = (
                sorted(folder.glob("*.zip"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
                if folder.is_dir() else []
            )
        except OSError:
            candidates = []
        for path in candidates:
            try:
                st = path.stat()
            except OSError:
                continue
            if pat not in path.name.lower():
                continue
            sizes_now[str(path)] = (st.st_size, int(st.st_mtime))
        for path_str, (size, mtime) in sizes_now.items():
            prev = last_sizes.get(path_str)
            if prev is not None and prev[0] == size and size > 0:
                if progress_cb:
                    progress_cb(
                        f"Found {Path(path_str).name} ({size // 1024} KB)")
                return Result.success(
                    data={"path": path_str, "size": size,
                          "mtime": mtime},
                    message=f"Downloaded {Path(path_str).name}")
        last_sizes = sizes_now
        now = time.time()
        if progress_cb and now - last_beat >= _WATCH_HEARTBEAT_S:
            last_beat = now
            progress_cb(
                f"Still waiting for *{pat}*.zip in {folder} …")
        time.sleep(_WATCH_POLL_S)
    return Result.failure(
        f"No *{pat}*.zip appeared in {folder} within {int(timeout)}s")


# ------------------------------------------------------------- public mirror


def fetch_search(query: str = "", order: str = "Relevance",
                 category: str = "", series: str = "", price: str = "",
                 author: str = "", page: int = 1, db_path=None) -> Result:
    """Remote search/browse (cached). Returns {items, total, page}."""
    if order not in ORDERS:
        return Result.failure(
            f"Unknown sort '{order}' (valid: {', '.join(ORDERS)})")
    if price and price not in PRICES:
        return Result.failure(
            f"Unknown price filter '{price}' (valid: {', '.join(PRICES)})")
    if category:
        path = f"/apps/modules/category/{urllib.parse.quote(category)}/browse"
    else:
        path = "/apps/modules/browse"
    if page > 1:
        path += f"/page/{int(page)}"
    params = []
    if query.strip():
        params.append(("search", query.strip()))
    if order != "Relevance":
        params.append(("order", order))
    if series:
        params.append(("series", series))
    if price:
        params.append(("price", price))
    if author:
        params.append(("author", author))
    if params:
        path += "?" + urllib.parse.urlencode(params)
    res = http_get(path, ttl=_TTL_SEARCH, db_path=db_path)
    if not res.ok:
        return res
    items = parse_cards(res.data)
    total = parse_total(res.data)
    return Result.success(
        data={"items": items, "total": total, "page": int(page),
              "categories": parse_categories(res.data),
              "note": res.message},
        message=f"{len(items)} of {total or len(items)} apps"
        if items else "No apps found",
    )


def fetch_detail(series: str, tech: str, db_path=None) -> Result:
    if not series or not tech:
        return Result.failure("Missing module series/technical name")
    path = f"/apps/modules/{urllib.parse.quote(series)}/" \
           f"{urllib.parse.quote(tech)}"
    res = http_get(path, ttl=_TTL_DETAIL, db_path=db_path)
    if not res.ok:
        return res
    detail = parse_detail(res.data, series)
    if not detail.get("tech"):
        return Result.failure(
            f"Module '{tech}' not found for {series} "
            "(page had no technical name — store layout drift?)")
    detail["note"] = res.message
    return Result.success(data=detail, message=f"{detail['title']}")


def fetch_charts(db_path=None) -> Result:
    res = http_get("/", ttl=_TTL_CHARTS, db_path=db_path)
    if not res.ok:
        return res
    return Result.success(data=parse_charts(res.data),
                          message=res.message)


def site_total(db_path=None) -> int:
    res = http_get("/apps/modules/browse", ttl=_TTL_STATS, db_path=db_path)
    if res.ok:
        return parse_total(res.data)
    return 0


def search(query: str = "", order: str = "Relevance", category: str = "",
           series: str = "", price: str = "", author: str = "",
           page: int = 1, db_path=None) -> Result:
    """Public search: remote mirror + GitHub rows, cache-stale fallback."""
    remote = fetch_search(query, order, category, series, price, author,
                          page, db_path=db_path)
    github_rows = search_local(query, source="github", db_path=db_path)
    if remote.ok:
        items = remote.data["items"]
        total = remote.data["total"]
        offline = False
        # official flag: publisher heuristic, applied on upsert of rows
        for item in items:
            item["official"] = item.get("author", "") == "Odoo S.A."
        upsert_modules([_row_for_storage(i) for i in items], db_path)
        note = remote.data.get("note", "")
    else:
        items = search_local(query, source="mirror", db_path=db_path)
        total = len(items)
        offline = True
        note = remote.message
    items = items + github_rows
    return Result.success(
        data={"items": items, "total": total, "page": int(page),
              "offline": offline, "note": note},
        message=note or f"{len(items)} apps",
    )


def _row_for_storage(item: dict) -> dict:
    import json as _json
    row = dict(item)
    row.setdefault("id", f"{row.get('series', '')}/{row.get('tech', '')}")
    row["tech_name"] = row.get("tech") or row.get("tech_name", "")
    if "versions" in row:
        row["versions_json"] = _json.dumps(row.get("versions") or [])
        del row["versions"]
    if "depends" in row:
        row["depends_json"] = _json.dumps(row.get("depends") or [])
        del row["depends"]
    if "manifest" in row:
        row["manifest_json"] = _json.dumps(row.get("manifest") or {})
        del row["manifest"]
    # storage-only aliases
    for key in ("tech", "local_reviews", "note", "screenshots", "available",
                "dl_version", "offline"):
        row.pop(key, None)
    return row


def detail(module_id: str, db_path=None) -> Result:
    """Detail by id: 'series/tech' (mirror) or 'gh:<repo>:<tech>'."""
    if module_id.startswith("gh:"):
        row = get_module(module_id, db_path)
        if row is None:
            return Result.failure(f"No indexed module '{module_id}'")
        row["local_reviews"] = list_reviews(module_id, db_path)
        row["description_html"] = sanitize_html(row.get("description_html", ""))
        row["depends"] = [
            {"label": d, "tech": d}
            for d in (row.get("depends") or [])
            if isinstance(d, str)
        ] or [
            {"label": d.get("tech", ""), "tech": d.get("tech", "")}
            for d in (row.get("depends") or []) if isinstance(d, dict)
        ]
        return Result.success(data=row, message=row.get("title", ""))
    parts = module_id.split("/", 1)
    if len(parts) != 2:
        return Result.failure(f"Bad module id '{module_id}'")
    res = fetch_detail(parts[0], parts[1], db_path=db_path)
    if not res.ok:
        # stale detail from cache rows if the fetch died hard
        row = get_module(module_id, db_path)
        if row is not None and row.get("description_html"):
            res = Result.success(data=row, message="cached copy")
        else:
            return res
    stored = dict(res.data)
    stored["official"] = stored.get("author", "") == "Odoo S.A."
    upsert_modules([_row_for_storage(stored)], db_path)
    res.data["local_reviews"] = list_reviews(module_id, db_path)
    row = get_module(module_id, db_path)
    if row is not None:
        # keep flags/counts we stored (charts sync, recorded downloads)
        for key in ("official", "featured", "category", "purchases",
                    "downloads"):
            if row.get(key) not in (None, "", 0, False):
                res.data[key] = row[key]
    return res


def stats(db_path=None) -> Result:
    data = db_stats(db_path)
    data["site_total"] = site_total(db_path)
    data["base_url"] = base_url()
    return Result.success(
        data=data,
        message=f"{data['site_total'] or data['cached']} apps available",
    )


def categories(db_path=None) -> Result:
    res = http_get("/apps/modules/browse", ttl=_TTL_STATS, db_path=db_path)
    names = parse_categories(res.data) if res.ok else []
    if not names:
        names = db_stats(db_path)["categories"]
    return Result.success(data=names, message=f"{len(names)} categories")


def sync_featured(db_path=None) -> Result:
    """Flag modules found in the home Top/New/Downloaded charts."""
    res = fetch_charts(db_path)
    if not res.ok:
        return res
    flagged, all_ids = [], set()
    for section_ids in res.data.values():
        all_ids.update(section_ids)
    for module_id in all_ids:
        # store rows only exist once seen; charts can flag on sight
        row = get_module(module_id, db_path)
        if row is not None and not row.get("featured"):
            flagged.append(module_id)
        elif row is None:
            # remember the chart membership even before first detail view
            series, tech = module_id.split("/", 1)
            upsert_modules([{
                "id": module_id, "series": series, "tech_name": tech,
                "title": tech, "featured": 1, "source": "mirror",
            }], db_path)
            flagged.append(module_id)
    with _connect(db_path) as conn:
        for module_id in flagged:
            conn.execute(
                "UPDATE marketplace_modules SET featured = 1 WHERE id = ?",
                (module_id,))
        conn.commit()
    return Result.success(
        data={"featured": sorted(all_ids)},
        message=f"{len(all_ids)} chart app(s) flagged featured",
    )


# ------------------------------------------------------------- github index

_REPO_RE = re.compile(r"^[\w.-]+/[\w.-]+$")


def _run_git(args: list[str], timeout: int = 120,
             progress_cb=None) -> Result:
    from odoo_vite.core.proc import run_streaming

    def _cb(line: str) -> None:
        if progress_cb is not None:
            try:
                progress_cb(line)
            except Exception:
                pass

    res = run_streaming(["git", *args], progress_cb=_cb, timeout=timeout)
    if not res.ok:
        return Result.failure(f"git {' '.join(args[:2])} failed: "
                              f"{res.message}")
    return res


def _default_branch(repo_url: str, progress_cb=None) -> Result:
    git = shutil.which("git")
    if git is None:
        return Result.failure("git is not installed (required to index)")
    try:
        proc = subprocess.run(
            [git, "ls-remote", "--symref", repo_url, "HEAD"],
            capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        return Result.failure("Timed out contacting github.com")
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "unknown").strip().splitlines()
        return Result.failure(
            f"Cannot reach repository: {err[0] if err else 'exit 1'}")
    m = re.search(r"ref:\s+refs/heads/(\S+)\s+HEAD", proc.stdout)
    if not m:
        return Result.failure("Repository has no HEAD branch")
    if progress_cb:
        try:
            progress_cb(f"Default branch: {m.group(1)}")
        except Exception:
            pass
    return Result.success(data=m.group(1), message=m.group(1))


def _parse_manifest(text: str) -> dict | None:
    try:
        data = ast.literal_eval(text.strip())
        return data if isinstance(data, dict) else None
    except (ValueError, SyntaxError):
        return None


def _series_from_version(version: str) -> str:
    parts = str(version or "").split(".")
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        return f"{parts[0]}.{parts[1]}"
    return ""


def index(owner_repo: str, branch: str = "", progress_cb=None,
          cancel=None, db_path=None) -> Result:
    """Index a GitHub repo (owner/repo) into the local marketplace.

    Shallow-clones (or refreshes) the repo into the marketplace cache,
    scans for __manifest__.py, and upserts source='github' rows with
    manifest metadata + README as description.
    """
    source = (owner_repo or "").strip().strip("/")
    if source.startswith("https://github.com/"):
        source = source[len("https://github.com/"):]
    if not _REPO_RE.match(source):
        return Result.failure(
            "Expected a GitHub repo as owner/repo (e.g. OCA/web-responsive)")
    if shutil.which("git") is None:
        return Result.failure("git is not installed (required to index)")
    if cancel is not None and cancel():
        return Result.failure("Cancelled")

    repo_url = f"{github_base()}/{source}"
    if not branch:
        bres = _default_branch(repo_url, progress_cb)
        if not bres.ok:
            return bres
        branch = bres.data

    dest = cache_dir() / source
    dest.parent.mkdir(parents=True, exist_ok=True)

    if (dest / ".git").exists():
        if progress_cb:
            progress_cb(f"Refreshing {source}@{branch} …")
        res = _run_git(["-C", str(dest), "fetch", "--depth", "1",
                        "origin", branch],
                       progress_cb=progress_cb, timeout=600)
        if not res.ok:
            return res
        res = _run_git(["-C", str(dest), "reset", "--hard",
                        "FETCH_HEAD"], progress_cb=progress_cb)
        if not res.ok:
            return res
    else:
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        if progress_cb:
            progress_cb(f"Cloning {source}@{branch} …")
        res = _run_git(["clone", "--depth", "1", "--branch", branch,
                        repo_url, str(dest)],
                       progress_cb=progress_cb, timeout=1800)
        if not res.ok:
            return res
    if cancel is not None and cancel():
        return Result.failure("Cancelled")

    manifests: list[tuple[str, dict]] = []
    for path in sorted(dest.rglob("__manifest__.py")):
        rel = path.parent.relative_to(dest)
        if any(part in (".git", "venv", ".venv", "node_modules")
               for part in rel.parts):
            continue
        if len(rel.parts) > 4:
            continue
        data = _parse_manifest(path.read_text(encoding="utf-8", errors="replace"))
        if data is None:
            if progress_cb:
                progress_cb(f"Skipping unparseable manifest: {rel}")
            continue
        manifests.append((str(rel), data))
    if not manifests:
        return Result.failure(
            f"No __manifest__.py found in {source} — not an Odoo addon repo")
    if cancel is not None and cancel():
        return Result.failure("Cancelled")

    rows = []
    for rel, manifest in manifests:
        tech = Path(rel).name if rel != "." else source.split("/")[1]
        readme_text = ""
        for cand in (dest / rel / "README.md", dest / rel / "readme.md",
                     dest / "README.md"):
            if cand.is_file():
                readme_text = cand.read_text(encoding="utf-8",
                                             errors="replace")[:200000]
                break
        description = str(manifest.get("description")
                          or manifest.get("summary") or "")
        summary = str(manifest.get("summary") or
                      description.split("\n")[0][:200])
        version = str(manifest.get("version") or "")
        icon = dest / rel / "static" / "description" / "icon.png"
        cover = ""
        if icon.is_file():
            sub = "" if rel == "." else f"{rel}/"
            cover = (f"https://raw.githubusercontent.com/{source}/{branch}/"
                     f"{sub}static/description/icon.png")
        rows.append({
            "id": f"gh:{source}:{tech}",
            "tech_name": tech,
            "series": _series_from_version(version),
            "title": str(manifest.get("name") or tech),
            "summary": summary,
            "author": str(manifest.get("author") or ""),
            "price": "FREE",
            "category": str(manifest.get("category") or ""),
            "cover_url": cover,
            "detail_url": f"https://github.com/{source}/tree/{branch}/{rel}"
            if rel != "." else f"https://github.com/{source}",
            "source": "github",
            "official": False,
            "featured": False,
            "free": True,
            "license": str(manifest.get("license") or ""),
            "repo_url": f"https://github.com/{source}",
            "description_html": readme_text,
            "depends": [d for d in (manifest.get("depends") or [])
                        if isinstance(d, str)],
            "versions": [v for v in [_series_from_version(version)] if v],
            "manifest": manifest,
        })
        if progress_cb:
            try:
                progress_cb(f"Indexed {tech} ({manifest.get('name', '')})")
            except Exception:
                pass

    # side flag: same technical name also on the store? keep rows separate.
    res = upsert_modules([_row_for_storage(r) for r in rows], db_path)
    if not res.ok:
        return res
    return Result.success(
        data={"modules": [r["tech_name"] for r in rows],
              "repo": repo_url, "branch": branch},
        message=f"Indexed {len(rows)} module(s) from {source}",
    )


# ------------------------------------------------------------------ install


def _zip_member_ok(name: str) -> bool:
    if not name or name.startswith("/") or name.startswith("\\"):
        return False
    parts = re.split(r"[\\/]", name)
    return ".." not in parts and not any(p.endswith(":") for p in parts[:1])


def _find_module_roots(staging: Path) -> list[Path]:
    """Module dirs in the staged tree (zip root or one folder down)."""
    if (staging / "__manifest__.py").is_file():
        return [staging]
    roots = [p for p in staging.iterdir()
             if p.is_dir() and (p / "__manifest__.py").is_file()]
    if roots:
        return sorted(roots)
    # repo-style zip: modules nested one more level (e.g. addons/<mod>)
    for child in staging.iterdir():
        if child.is_dir():
            roots = [p for p in child.iterdir()
                     if p.is_dir() and (p / "__manifest__.py").is_file()]
            if roots:
                return sorted(roots)
    return []


def import_addon_zip(zip_path: str | Path, instance_id: str,
                     db_path=None) -> Result:
    """Safely unpack an addon zip into <instance>/addons + register it.

    Validates every member (no absolutes/'..'/symlinks, size caps),
    discovers module roots by __manifest__.py, refuses to overwrite an
    existing module folder, then appends the target dir to the
    instance's addons_path via apply_addons_state.
    """
    from odoo_vite.core import addon_paths
    from odoo_vite.core.registry import get_instance

    zip_path = Path(zip_path).expanduser()
    if not zip_path.is_file():
        return Result.failure(f"No such file: {zip_path}")
    size = zip_path.stat().st_size
    if size > MAX_ZIP_BYTES:
        return Result.failure(
            f"Archive too large ({size // (1024 * 1024)} MB > "
            f"{MAX_ZIP_BYTES // (1024 * 1024)} MB)")
    if not zipfile.is_zipfile(zip_path):
        return Result.failure("Not a zip archive (did the download finish?)")
    inst = get_instance(instance_id, db_path)
    if inst is None:
        return Result.failure(f"No instance with id '{instance_id}'")
    if not inst.path:
        return Result.failure("Instance has no folder on disk")

    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        return Result.failure(f"Corrupt zip: {exc}")
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            return Result.failure(
                f"Archive has too many entries ({len(infos)})")
        total = sum(i.file_size for i in infos)
        if total > MAX_ZIP_UNCOMPRESSED:
            return Result.failure("Archive expands beyond the size limit")
        safe = []
        for info in infos:
            if not _zip_member_ok(info.filename):
                return Result.failure(
                    f"Unsafe archive entry rejected: {info.filename!r}")
            mode = info.external_attr >> 16
            if (mode & 0o170000) == 0o120000:
                return Result.failure(
                    f"Symlink entry rejected: {info.filename!r}")
            safe.append(info)

        staging = Path(tempfile.mkdtemp(prefix="ov-mkt-"))
        try:
            zf.extractall(staging)
            roots = _find_module_roots(staging)
            if not roots:
                return Result.failure(
                    "No Odoo module found in the archive "
                    "(no __manifest__.py)")
            target = Path(inst.path) / "addons"
            target.mkdir(parents=True, exist_ok=True)
            installed: list[str] = []
            pending: list[Path] = []
            for root in roots:
                name = root.name if root != staging else ""
                if root == staging:
                    # module at zip root: name it after its folder guess
                    manifest = _parse_manifest(
                        (staging / "__manifest__.py").read_text(
                            encoding="utf-8", errors="replace"))
                    name = str((manifest or {}).get("name") or "").strip()
                    name = re.sub(r"[^\w]+", "_", name).lower().strip("_") \
                        or "imported_module"
                dest = target / name
                if dest.exists():
                    return Result.failure(
                        f"Module folder already exists: {dest} "
                        "(remove it or update via Modules instead)")
                pending.append(dest)
                installed.append(name)
            for root, dest in zip(roots, pending):
                shutil.move(str(root), str(dest))
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    entries = addon_paths.get_addons_state(inst)
    if not any(Path(e["path"]).resolve() == target.resolve()
               for e in entries):
        entries.append({"path": str(target), "enabled": True})
        applied = addon_paths.apply_addons_state(instance_id, entries,
                                                 db_path)
        if not applied.ok:
            return Result.failure(
                f"Modules unpacked to {target} but the addons_path "
                f"update failed: {applied.message}")
    return Result.success(
        data={"modules": installed, "path": str(target)},
        message=f"Added {', '.join(installed)} to {inst.name}",
    )
