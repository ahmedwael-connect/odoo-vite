"""Selection state (PSS-2): pure logic behind the Slint SelectionList view.

Mirrors the Qt SelectionList behavioral contract (grouped rows, filter,
check preservation across refreshes, counts), minus anything Qt-specific.
Pure Python — no slint import — so it unit-tests without a UI thread.
The bridge pushes visible_rows() into a slint.ListModel; user toggles
come back through callbacks into set_checked().
"""

from __future__ import annotations


def flatten(rows: list[dict]) -> list[dict]:
    """Group rows like the Qt component: ungrouped first, then one header
    row per group in first-seen order. Headers never match by id."""
    flat: list[dict] = []
    seen: list[str] = []
    by_group: dict[str, list[dict]] = {}
    ungrouped: list[dict] = []
    for row in rows:
        group = str(row.get("group") or "")
        if group:
            if group not in by_group:
                by_group[group] = []
                seen.append(group)
            by_group[group].append(row)
        else:
            ungrouped.append(row)
    flat.extend(ungrouped)
    for group in seen:
        flat.append({"id": f"__group:{group}", "title": group,
                     "badge": "", "checked": False, "header": True})
        flat.extend(by_group[group])
    return flat


class SelectionState:
    """Refresh-safe picker state (single or multi)."""

    def __init__(self, multi: bool = True) -> None:
        self._multi = multi
        self._rows: list[dict] = []
        self._needle = ""
        self._current_id = None

    # ------------------------------------------------------------ inputs

    def set_items(self, rows: list[dict]) -> None:
        """Replace rows; keep checks (by id), filter text, and cursor."""
        keep_checks = {r["id"]: bool(r.get("checked"))
                       for r in self._rows if not r.get("header")}
        self._rows = []
        for row in flatten(rows):
            entry = dict(row)
            entry.setdefault("badge", "")
            entry.setdefault("checked", False)
            entry.setdefault("header", False)
            if (self._multi and not entry["header"]
                    and "checked" not in row):
                entry["checked"] = keep_checks.get(entry["id"], False)
            if not self._multi:
                entry["checked"] = False
            self._rows.append(entry)
        if (self._current_id is not None and not any(
                r["id"] == self._current_id and not r.get("header")
                for r in self._rows)):
            self._current_id = None

    def set_filter(self, needle: str) -> None:
        self._needle = (needle or "").strip().lower()

    def set_checked(self, item_id, checked: bool) -> bool:
        if not self._multi:
            return False
        for row in self._rows:
            if row["id"] == item_id and not row.get("header"):
                row["checked"] = bool(checked)
                return True
        return False

    def move_current(self, item_id) -> bool:
        """Keyboard cursor (never a header). Returns found."""
        for row in self._rows:
            if row["id"] == item_id and not row.get("header"):
                self._current_id = item_id
                return True
        return False

    # ------------------------------------------------------------ outputs

    def visible_rows(self) -> list[dict]:
        """Rows matching the filter (headers always shown with their group
        only when at least one member matches — simplified: headers shown
        when unfiltered or when any visible member follows)."""
        if not self._needle:
            return [dict(r) for r in self._rows]
        out: list[dict] = []
        pending_header: dict | None = None
        pending_members: list[dict] = []
        def _matches(row: dict) -> bool:
            hay = (str(row.get("title", "")) + " "
                   + str(row.get("badge", ""))).lower()
            return self._needle in hay
        def _flush() -> None:
            if pending_members:
                out.append(pending_header)
                out.extend(pending_members)
        for row in self._rows:
            if row.get("header"):
                _flush()
                pending_header = dict(row)
                pending_members = []
            elif _matches(row):
                if pending_header is None:
                    out.append(dict(row))
                else:
                    pending_members.append(dict(row))
        _flush()
        return out

    def checked_ids(self) -> list:
        return [r["id"] for r in self._rows
                if r.get("checked") and not r.get("header")]

    def view_rows(self) -> list[dict]:
        """visible_rows() trimmed to the .slint Row struct — struct
        conversion rejects unknown keys (e.g. the grouping hint)."""
        return [{"id": r["id"], "title": str(r.get("title", "")),
                 "badge": str(r.get("badge", "")),
                 "checked": bool(r.get("checked", False)),
                 "header": bool(r.get("header", False))}
                for r in self.visible_rows()]

    def counts_text(self) -> str:
        total = sum(1 for r in self._rows if not r.get("header"))
        visible = sum(1 for r in self.visible_rows()
                      if not r.get("header"))
        if self._multi:
            checked = len(self.checked_ids())
            return f"{checked} checked · {visible} of {total} shown"
        if self._needle:
            return f"{visible} of {total} shown"
        return ""

    @property
    def current_id(self):
        return self._current_id


def sync_model(model, desired: list[dict]) -> None:
    """In-place reactive sync of a slint.ListModel of structs.

    Index-wise writes only where content differs, then length-adjust —
    unchanged rows keep identity (no drop, no repaint, cursor/scroll
    survive). Replacing the whole model every poll drops struct-holding
    values on GC, which races Slint-Python's thread checks when worker
    threads exist (verified abort); this is also just faster.
    """
    for i, row in enumerate(desired):
        if i < len(model):
            if dict(model[i]) != row:
                model[i] = row
        else:
            model.append(row)
    while len(model) > len(desired):
        del model[len(model) - 1]


def sync_strings(model, desired: list[str]) -> None:
    """sync_model for plain string lists (combo boxes): element-wise
    writes only on change, then length-adjust. Same no-drop rationale."""
    for i, value in enumerate(desired):
        if i < len(model):
            if model[i] != value:
                model[i] = value
        else:
            model.append(value)
    while len(model) > len(desired):
        del model[len(model) - 1]
