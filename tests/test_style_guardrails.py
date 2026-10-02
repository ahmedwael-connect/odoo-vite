"""Mechanical style guardrails for the web frontend.

Replaces the deleted Slint style suite (slint-remake-plan RM-0): the
rules docs/design-system.md states must be checkable, not aspirational.

1. hex colors only inside theme blocks (:root / [data-theme=...])
2. font-size only inside theme blocks or via var(--fs-*)
3. margin/padding/gap px values only from the spacing scale
   4/8/12/16/24/32 (1px/2px hairlines allowed)
"""

from __future__ import annotations

import re
from pathlib import Path

STYLE = (
    Path(__file__).resolve().parents[1]
    / "odoo_vite" / "ui_web" / "frontend" / "src" / "style.css"
)

SCALE = {0, 1, 2, 4, 8, 12, 16, 24, 32}

DECL_RE = re.compile(
    r"(margin(?:-(?:top|bottom|left|right))?|"
    r"padding(?:-(?:top|bottom|left|right))?|"
    r"(?:row|column)-?gap|gap)\s*:"
)


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _blocks(css: str) -> tuple[list[str], str]:
    """Split into (theme-block bodies, rest-with-bodies-removed).

    A block is 'theme' when its selector mentions :root or [data-theme.
    Naive brace matching is fine: style.css has no strings or nested
    at-rule blocks with braces inside selector lists.
    """
    theme: list[str] = []
    rest: list[str] = []
    pos = 0
    for m in re.finditer(r"([^{}]+)\{", css):
        if m.start() < pos:
            continue  # selector we already consumed as part of a block body
        open_idx = m.end() - 1
        depth = 0
        end = None
        for i in range(open_idx, len(css)):
            if css[i] == "{":
                depth += 1
            elif css[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end is None:
            break
        selector = m.group(1).strip()
        body = css[m.end() : end]
        rest.append(css[pos : m.start()])
        if ":root" in selector or "[data-theme" in selector:
            theme.append(body)
        else:
            rest.append(selector + "{" + body + "}")
        pos = end + 1
    rest.append(css[pos:])
    return theme, "".join(rest)


def test_style_css_exists_and_balances():
    css = _strip_comments(STYLE.read_text())
    assert css.count("{") == css.count("}"), "unbalanced braces in style.css"


def test_hex_colors_only_in_theme_blocks():
    css = _strip_comments(STYLE.read_text())
    theme_bodies, rest = _blocks(css)
    hexes = re.findall(r"#[0-9a-fA-F]{3,8}\b", rest)
    assert not hexes, (
        f"hex color(s) outside :root/[data-theme] blocks: {sorted(set(hexes))} "
        "-- use an existing token or add the role to both theme blocks"
    )
    assert theme_bodies, "no theme blocks found — parser or file changed?"


def test_font_size_only_via_tokens():
    css = _strip_comments(STYLE.read_text())
    _theme, rest = _blocks(css)
    bad = [
        line.strip()
        for line in rest.splitlines()
        if "font-size:" in line and "var(--fs-" not in line
    ]
    assert not bad, (
        f"literal font-size outside theme blocks: {bad} "
        "-- use var(--fs-display|title|heading|body|label|caption|mono)"
    )


def test_spacing_from_scale():
    css = _strip_comments(STYLE.read_text())
    _theme, rest = _blocks(css)
    bad: list[str] = []
    for m in DECL_RE.finditer(rest):
        # slice the declaration (up to the terminating ';')
        end = rest.find(";", m.end())
        decl = rest[m.start() : end if end != -1 else m.end() + 60]
        for px in re.findall(r"(-?\d+)px", decl):
            if abs(int(px)) not in SCALE:
                bad.append(decl.strip().splitlines()[0][:80])
                break
    assert not bad, (
        f"off-scale spacing (allowed 4/8/12/16/24/32, hairline 1/2): {bad}"
    )
