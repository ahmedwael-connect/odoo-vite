"""Log doctor (Sprint 8, Ticket B.3): known failure signatures in plain language.

Small, high-confidence set — each finding carries severity + an actionable
suggestion, or the raw excerpt when nothing specific applies. The
pkg_resources signature is dogfooded from this project's own history.
No GTK imports.
"""

from __future__ import annotations

import os
import re

SIGNATURES = [
    {
        "id": "pkg_resources",
        "severity": "high",
        "title": "Python venv is missing setuptools (pkg_resources)",
        "match": re.compile(r"ModuleNotFoundError: No module named 'pkg_resources'"),
        "suggestion": ("The instance venv was built without setuptools "
                       "(Python 3.12+ venvs omit it; setuptools>=81 removed "
                       "pkg_resources entirely). Use this app's 'Repair venv' "
                       "action (installs setuptools<81 + wheel), then Start again."),
    },
    {
        "id": "db_connection",
        "severity": "high",
        "title": "Odoo cannot reach PostgreSQL",
        "match": re.compile(
            r"could not connect to server|connection refused|"
            r"connection to server .* failed|FATAL:\s+(database|role|password)",
            re.IGNORECASE),
        "suggestion": ("Check Postgres is running (pg_lsclusters), then the "
                       "conf's db_host/db_port and the role/password "
                       "(this app's Databases → Validate config checks all three)."),
    },
    {
        "id": "port_in_use",
        "severity": "high",
        "title": "Port already in use",
        "match": re.compile(
            r"Address already in use|Errno 98|error: \[Errno 98\]|"
            r"address in use", re.IGNORECASE),
        "suggestion": ("Another process holds this instance's port. Find it "
                       "with `ss -ltnp | grep <port>` and stop it, or change "
                       "xmlrpc_port on the Configuration tab."),
    },
    {
        "id": "permission_denied",
        "severity": "medium",
        "title": "Permission denied on a file or directory",
        "match": re.compile(
            r"Permission denied|PermissionError|EACCES", re.IGNORECASE),
        "suggestion": ("A path Odoo needs (addons, logfile, data_dir) isn't "
                       "writable by the user running odoo-bin. Check ownership "
                       "with `ls -la` on the instance folder."),
    },
    {
        "id": "uninitialized_db",
        "severity": "medium",
        "title": "Database exists but was never initialized",
        "match": re.compile(
            r"Database .* not initialized, you can force it with `-i base`"),
        "suggestion": ("Run Initialize on this database (Databases tab), "
                       "then Start again."),
    },
]


def diagnose_file(path: str, max_findings: int = 50) -> list[dict]:
    """Scan a log file, return findings newest-last.

    Each: {severity, title, suggestion, excerpt, lineno}. Traceback blocks
    without a specific signature become one generic unhandled-exception
    finding (never silent).
    """
    findings: list[dict] = []
    if not os.path.isfile(path):
        return findings
    in_traceback = False
    tb_start = 0
    tb_lines: list[str] = []
    lineno = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                lineno += 1
                if len(raw) > 2000:
                    raw = raw[:2000] + "…\n"
                line = raw.rstrip("\n")
                if line.strip() == "Traceback (most recent call last):":
                    in_traceback = True
                    tb_start = lineno
                    tb_lines = []
                    continue
                if in_traceback:
                    if line.startswith(("  ", "\t")) or line.startswith("File "):
                        tb_lines.append(line)
                        continue
                    # block ends at the exception line (first non-indented)
                    excerpt = (tb_lines[-3:] + [line])[-4:] if line.strip() else tb_lines[-3:]
                    _maybe_add(findings, "traceback", "medium",
                               "Unhandled exception (traceback)",
                               "A Python traceback with no specific signature matched. "
                               "Read the exception line and the frames above it.",
                               excerpt, tb_start, max_findings)
                    in_traceback = False
                    tb_lines = []
                    # fall through: the exception line itself may match a signature
                for sig in SIGNATURES:
                    if sig["match"].search(line):
                        _maybe_add(findings, sig["id"], sig["severity"],
                                   sig["title"], sig["suggestion"],
                                   [line.strip()[:300]], lineno, max_findings)
                        break
                if len(findings) >= max_findings:
                    break
    except OSError:
        return findings
    return findings


def _maybe_add(findings: list, sig_id: str, severity: str, title: str,
               suggestion: str, excerpt: list, lineno: int,
               max_findings: int) -> None:
    # de-dupe repeats of the same signature (log floods repeat the same error)
    for existing in findings:
        if existing.get("sig_id") == sig_id:
            existing["count"] = existing.get("count", 1) + 1
            existing["last_lineno"] = lineno
            return
    if len(findings) >= max_findings:
        return
    findings.append({"sig_id": sig_id, "severity": severity, "title": title,
                     "suggestion": suggestion, "excerpt": excerpt,
                     "lineno": lineno, "count": 1})
