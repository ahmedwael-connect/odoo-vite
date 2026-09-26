"""Logs page (PSQ-7.2): faithful port of the GTK Logs tab.

Tail (virtualized QListView, 5000-row cap), Follow toggle with the full
cycle (follow → scroll-up auto-pause → resume at bottom), Clear view,
search + level filter, doctor findings, slow-query list, profiler lane.
Display polling (LogFollower + 1s QTimer) lives here, like GTK's page —
file reads are cheap and synchronous; heavy work (search/doctor/slow/
profile) belongs to LogFlows workers.
"""

from PySide6.QtCore import QTimer, Signal, QStringListModel, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from odoo_vite.core import log_tail  # noqa: E402
from odoo_vite.core.registry import get_instance  # noqa: E402

LOG_LEVELS = ["All levels", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
PROFILE_DURATIONS = ["5s", "10s", "30s"]
LOG_MODEL_CAP = 5000


class LogsPage(QWidget):
    actionRequested = Signal(str, str, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._instance_id: str | None = None
        self._log_path: str = ""
        self._follower = None
        self._follow = True
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(16, 12, 16, 16)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        title = QLabel("Logs")
        title.setProperty("class", "heading")
        toolbar.addWidget(title, 1)
        self.btn_follow = QPushButton("Follow")
        self.btn_follow.setCheckable(True)
        self.btn_follow.setChecked(True)
        self.btn_follow.setToolTip(
            "Follow new lines as they arrive (pauses automatically when "
            "you scroll up to read)")
        self.btn_follow.toggled.connect(self._on_follow_toggled)
        toolbar.addWidget(self.btn_follow)
        self.btn_clear = QPushButton("Clear view")
        self.btn_clear.setToolTip(
            "Clear the displayed rows only (the log file on disk is "
            "untouched — new lines keep arriving)")
        self.btn_clear.clicked.connect(self._on_clear_view)
        toolbar.addWidget(self.btn_clear)
        self.btn_doctor = QPushButton("Run Doctor")
        self.btn_doctor.setToolTip("Scan the log for known failure signatures")
        self.btn_doctor.clicked.connect(
            lambda: self._emit("log-doctor", None))
        toolbar.addWidget(self.btn_doctor)
        self.drop_profile_dur = QComboBox()
        self.drop_profile_dur.addItems(PROFILE_DURATIONS)
        self.drop_profile_dur.setCurrentIndex(1)
        toolbar.addWidget(self.drop_profile_dur)
        self.btn_profile = QPushButton("Profile")
        self.btn_profile.setToolTip(
            "Record a py-spy flame graph of the running process")
        self.btn_profile.clicked.connect(self._on_profile_clicked)
        toolbar.addWidget(self.btn_profile)
        layout.addLayout(toolbar)

        self.lbl_paused = QLabel(
            "⏸ not following — scroll to the bottom or toggle Follow "
            "to resume")
        self.lbl_paused.setProperty("class", "warning")
        self.lbl_paused.setVisible(False)
        layout.addWidget(self.lbl_paused)
        self.lbl_note = QLabel()
        self.lbl_note.setProperty("class", "dim")
        self.lbl_note.setWordWrap(True)
        layout.addWidget(self.lbl_note)

        search_title = QLabel("Search (full file, streamed)")
        search_title.setProperty("class", "heading")
        layout.addWidget(search_title)
        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self.entry_search = QLineEdit()
        self.entry_search.setPlaceholderText("regex pattern…")
        self.entry_search.setClearButtonEnabled(True)
        search_row.addWidget(self.entry_search, 1)
        self.drop_level = QComboBox()
        self.drop_level.addItems(LOG_LEVELS)
        search_row.addWidget(self.drop_level)
        self.btn_search = QPushButton("Search")
        self.btn_search.clicked.connect(
            lambda: self._emit("log-search", None))
        search_row.addWidget(self.btn_search)
        layout.addLayout(search_row)
        self.lbl_search_status = QLabel()
        self.lbl_search_status.setProperty("class", "dim")
        layout.addWidget(self.lbl_search_status)
        self.search_results = QListWidget()
        self.search_results.setMaximumHeight(170)
        layout.addWidget(self.search_results)

        self.doctor_list = QListWidget()
        self.doctor_list.setVisible(False)
        self.doctor_list.setMaximumHeight(170)
        layout.addWidget(self.doctor_list)

        self.tail_model = QStringListModel(self)
        self.tail_view = QListView()
        self.tail_view.setModel(self.tail_model)
        self.tail_view.setUniformItemSizes(True)
        layout.addWidget(self.tail_view, 1)

        slow_title = QLabel("Slow queries (pg_stat_statements)")
        slow_title.setProperty("class", "heading")
        layout.addWidget(slow_title)
        slow_row = QHBoxLayout()
        slow_row.setSpacing(8)
        self.lbl_slow_status = QLabel()
        self.lbl_slow_status.setProperty("class", "dim")
        self.lbl_slow_status.setWordWrap(True)
        slow_row.addWidget(self.lbl_slow_status, 1)
        self.btn_slow = QPushButton("Refresh")
        self.btn_slow.clicked.connect(
            lambda: self._emit("slow-refresh", None))
        slow_row.addWidget(self.btn_slow)
        slow_row.addStretch(1)
        layout.addLayout(slow_row)
        self.slow_list = QListWidget()
        self.slow_list.setMaximumHeight(150)
        layout.addWidget(self.slow_list)

        self._poll = QTimer(self)
        self._poll.setInterval(1000)
        self._poll.timeout.connect(self._poll_tick)

    # ------------------------------------------------------------------ API

    def show_instance(self, instance) -> None:
        if isinstance(instance, dict):
            self._instance_id = instance.get("id")
        else:
            self._instance_id = instance.id
        self.start_poll()

    def start_poll(self) -> None:
        """(Re)start tailing the current instance's log file."""
        self.stop_poll()
        inst = (get_instance(self._instance_id)
                if self._instance_id else None)
        self._log_path = (inst.log_path or "") if inst else ""
        if not self._log_path:
            self.lbl_note.setText("No log file recorded.")
            return
        self._follower = log_tail.LogFollower(self._log_path)
        try:
            initial = log_tail.read_last_n(self._log_path, 500)
        except Exception:
            initial = []
        self._follower.sync_to_end()
        self.tail_model.setStringList(
            [line[:2000] for line in initial])
        self.lbl_note.setText(
            f"Tailing {self._log_path} (last {len(initial)} lines shown)")
        self._scroll_to_end()
        self._poll.start()

    def stop_poll(self) -> None:
        self._poll.stop()
        self._follower = None

    def clear_view(self) -> None:
        """Display only — the file on disk is untouched."""
        self.tail_model.setStringList([])

    def set_search_results(self, matches: list, message: str) -> None:
        self.search_results.clear()
        self.lbl_search_status.setText(message)
        for match in (matches or [])[:200]:
            head = f"line {match.get('lineno', '?')}: {match.get('line', '')}"
            self.search_results.addItem(head[:220])
            for ctx in (match.get("before", []) + match.get("after", []))[-4:]:
                item = QListWidgetItem(f"    {ctx}"[:220])
                item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
                self.search_results.addItem(item)

    def set_doctor_findings(self, findings: list) -> None:
        self.doctor_list.clear()
        self.doctor_list.setVisible(bool(findings))
        for finding in findings or []:
            count = finding.get("count", 1)
            mark = "!" if finding.get("severity") == "high" else "i"
            text = (f"[{mark}] {finding.get('title', '')}"
                    + (f"  (×{count})" if count > 1 else ""))
            self.doctor_list.addItem(text)

    def set_slow_queries(self, ok: bool, message: str, rows: list) -> None:
        self.slow_list.clear()
        self.lbl_slow_status.setText(message)
        for entry in (rows or [])[:20]:
            self.slow_list.addItem(
                f"{str(entry.get('query', ''))[:140]} — "
                f"{entry.get('calls', 0)} calls · "
                f"total {entry.get('total_ms', 0)} ms")

    # -------------------------------------------------------------- internals

    def _on_follow_toggled(self, following: bool) -> None:
        self._follow = following
        self.lbl_paused.setVisible(not following)
        if following:
            self._scroll_to_end()

    def _on_clear_view(self) -> None:
        self.clear_view()

    def _on_profile_clicked(self) -> None:
        text = self.drop_profile_dur.currentText() or "10s"
        try:
            duration = int("".join(c for c in text if c.isdigit()) or 10)
        except ValueError:
            duration = 10
        self._emit("profile", duration)

    def _poll_tick(self) -> None:
        follower = self._follower
        if follower is None:
            return
        try:
            batch = follower.poll()
        except Exception:
            return
        if batch.get("missing"):
            self.lbl_note.setText(f"Waiting for log file: {self._log_path}")
            return
        if batch.get("rotated"):
            self.tail_model.setStringList([])
            self.lbl_note.setText("Log rotated/truncated — restarted from top")
        lines = batch.get("lines", [])
        if lines:
            self._append_lines(lines)

    def _append_lines(self, lines: list) -> None:
        bar = self.tail_view.verticalScrollBar()
        follow = self._follow and self.btn_follow.isChecked()
        if follow and bar is not None:
            follow = (bar.value() >= bar.maximum() - 8)
        rows = self.tail_model.stringList()
        rows.extend(line[:2000] for line in lines)
        over = len(rows) - LOG_MODEL_CAP
        if over > 0:
            del rows[:over]
        self.tail_model.setStringList(rows)
        self.lbl_paused.setVisible(not follow)
        if follow:
            self._scroll_to_end()

    def _scroll_to_end(self) -> None:
        # QListView lays rows out lazily: a single setValue right after a
        # model reset lands mid-layout and sticks (same class as GTK F2.3).
        # scrollToBottom is layout-aware; the landing loop below converges
        # while layout is still settling (bounded, stops at bottom).
        try:
            self.tail_view.scrollToBottom()
        except Exception:
            pass
        bar = self.tail_view.verticalScrollBar()
        if bar is not None:
            try:
                bar.setValue(bar.maximum())
            except Exception:
                pass
        self._ensure_landing()

    def _ensure_landing(self, tries: int = 20) -> None:
        if getattr(self, "_landing", False):
            return
        self._landing = True
        state = {"tries": tries}

        def _tick() -> None:
            if not self._follow:
                self._landing = False
                return
            try:
                bar = self.tail_view.verticalScrollBar()
                landed = (bar is None or bar.value() >= bar.maximum() - 8)
            except Exception:
                landed = True
            state["tries"] -= 1
            if landed or state["tries"] <= 0:
                self._landing = False
                return
            try:
                self.tail_view.scrollToBottom()
            except Exception:
                pass
            QTimer.singleShot(150, _tick)

        QTimer.singleShot(150, _tick)

    def _emit(self, action: str, payload) -> None:
        if self._instance_id is not None:
            self.actionRequested.emit(action, self._instance_id, payload)

    def set_actions_enabled(self, enabled: bool) -> None:
        """Busy-state gating (GTK _set_actions_sensitive parity)."""
        for btn in (self.btn_doctor, self.btn_profile, self.btn_search,
                    self.btn_slow):
            try:
                btn.setEnabled(enabled)
            except Exception:
                pass
