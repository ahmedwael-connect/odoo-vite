# Odoo Vite — PM Decision: Phase 1 Retro & Phase 1.5 (Hardening) before Phase 2

**Decision:** Before any new feature work (addon manager, backups, upgrades), we run one **hardening sprint (Phase 1.5)**. This is the standard move for any tool that wants to be taken seriously as infrastructure — Docker, Portainer, Proxmox all separate "does it work" from "is it safe to run unattended / on a shared box / by someone other than the original author." We're at exactly that inflection point: Phase 1 proved the concept works end-to-end. Phase 1.5 makes it trustworthy. Phase 2 then builds on a foundation that won't need to be revisited.

I'm not sending this out for a developer retro first — the backlog is already fully visible from the four sprint reports, and enterprise posture is a PM call, not something to crowdsource from the implementer. Below is the consolidated backlog and the sprint spec.

---

## 1. Consolidated backlog (pulled from Sprints 1–4)

| # | Item | Source | Severity |
|---|---|---|---|
| 1 | DB role is `CREATEDB`-privileged unconditionally — no least-privilege mode | Sprint 2/3 Q&A | **High** — this is the single biggest thing standing between "dev tool" and "enterprise tool" |
| 2 | Remove only drops `primary_db`, leaves other tracked DBs as orphans | Sprint 4 | Medium |
| 3 | Odoo version→requirements matrix (15.0–18.0) marked TODO, never formally confirmed | Sprint 1 | Medium |
| 4 | `db_password` fallback is plaintext SQLite when no keyring/Secret Service is present (e.g. headless/minimal Ubuntu, some server installs) | Sprint 1/2 | **High** for enterprise — this is a real credential-at-rest exposure |
| 5 | No audit log rotation | Sprint 3/4 | Low (revisit once volume is real) |
| 6 | Error-pill states are minimal/reactive (added only when a real scenario demands it) | Sprint 4 | Low — working as intended, not a gap |
| 7 | Enterprise addons version-mismatch check never built (only flagged as a recommendation, never spec'd) | earlier conversation | Medium |
| 8 | No concept of "who did this" — single-user assumption baked in everywhere (registry, audit log, process ownership) | architectural, implicit | **High** for enterprise — see §2 |
| 9 | pkexec-based privilege escalation works for a single interactive desktop session; no story for headless/CI/server provisioning | Sprint 1/3 Q&A | Medium — matters if we ever want a CLI/server mode |

---

## 2. What "enterprise-level, Docker-like" actually requires (PM framing)

Docker Desktop/Portainer-class tools share a few properties this app doesn't have yet:

- **Least-privilege by default**, elevated privilege only for specific, logged, user-confirmed actions — not a standing broad grant.
- **Secrets never at rest in plaintext**, full stop — not "primary path is secure, fallback is plaintext." An enterprise tool either has a secrets story that always works, or it fails loudly and tells the user to fix their environment (install/enable a secret service) rather than silently degrading security.
- **Multi-instance safety on a shared machine** — orphaned resources (databases, ports, processes) don't accumulate silently. Item #2 is a small instance of a bigger pattern we should close off now while the surface area is still small.
- **Everything an audit log claims happened is independently verifiable** — not blocking for Phase 1.5, but rotation + eventual "who" field belongs on the same roadmap.

I am **not** including item #9 (headless/CLI/server mode) in Phase 1.5 — that's a legitimate Phase 3+ conversation (multi-user, remote management) and pulling it in now would blow up this sprint's scope for a capability nobody has asked for yet. Noting it in the backlog is sufficient for now.

---

## 3. Phase 1.5 Sprint Spec: Hardening

**Depends on:** Sprints 1–4 (all accepted).

### Ticket H.1 — Least-privilege DB role + Managed/Developer mode
- Add an app-level setting (simple key in a small `core/settings.py` / a `settings` table, your call — note which): `provisioning_mode` = `"developer"` (default, current behavior — `CREATEDB` granted) or `"managed"` (new).
- In `"managed"` mode: `db_manager.ensure_role()` creates the role **without** `CREATEDB`. Database creation/drop becomes an explicit, separately-privileged operation: prompt once for a Postgres superuser credential (or reuse `pkexec` against the `postgres` system user, consistent with the existing `ensure_role` pattern) at the moment a database actually needs to be created or dropped, rather than holding a standing grant.
- Store which mode each instance was created under (new column, e.g. `provisioning_mode` on `instances`) so it's visible per-instance and mixed-mode systems are legible, not just a global assumption.
- Surface the current mode clearly in Settings and in the "New Instance" wizard's review step (so the user knows what they're getting before they commit).
- **Do not migrate existing instances' privilege level automatically** — that's a live, potentially breaking change to something already running. Leave existing rows as-is; mode only applies going forward.

### Ticket H.2 — Secrets: fail loudly instead of silently degrading
- Remove the silent plaintext fallback. If keyring/Secret Service is unavailable at instance-creation time: **stop and tell the user**, with a clear explanation and (if feasible) a one-click suggestion (`sudo apt install gnome-keyring` or equivalent, detected per §Sprint 1's `system_check.py` pattern) rather than quietly writing a plaintext password to SQLite.
- Add an explicit, clearly-labeled opt-out: "I understand the risk, store this password in plaintext locally" checkbox — a deliberate choice, not a silent default. Log this choice to the audit log if taken.
- Sweep existing `password_storage="plaintext"` rows (if any exist from Sprint 1/2 testing) — not a forced migration, but flag them in the UI (small warning icon on the instance card: "password stored in plaintext — click to secure") with a one-click "move to keyring now" action.

### Ticket H.3 — Remove: full tracked-DB cleanup option
- Extend `removal.py`/Remove UI (from Sprint 4) so the confirmation dialog lists **all** `tracked_dbs`, not just `primary_db`, each with its own checkbox (all unchecked by default, consistent with "never default to destructive"). Drop whichever are checked.
- No change to the adopted-instance path (still never touches anything).

### Ticket H.4 — Odoo version/requirements matrix: confirm properly
- Actually verify the 15.0–18.0 requirements matrix from Sprint 1 against Odoo's own published system requirements (check each version's `requirements.txt` + Odoo's official installation docs) rather than leaving it as an unverified guess. This is a correctness/trust issue — the whole system-check feature is only as good as this table.
- Document the source for each entry (comment referencing where each requirement was confirmed) so it's maintainable later, not a black box.

### Ticket H.5 — Enterprise addon version-mismatch check
- From the earlier recommendation: when an `enterprise_path` is set (Create wizard step 4, or Adopt wizard), sample a manifest's `version` key from the enterprise folder and compare against the instance's Odoo version. Mismatch → clear warning (not a hard block — enterprise folder structures aren't 100% uniform across versions, false positives are possible), shown both at set-time and persistently on the instance detail page (badge, not just a one-time toast) per our toast/persistent-label pattern.

### Out of scope for Phase 1.5
- Headless/CLI/server provisioning mode (backlog, Phase 3+ conversation)
- Multi-user/"who did this" audit fields (backlog, same conversation)
- Audit log rotation (still not urgent — revisit when Phase 2 adds higher-volume actions like backups)
- Any new feature surface (addon manager, backups, upgrades — that's Phase 2, after this)

### Report-back template
```
Ticket H.1 (least-privilege mode): [done/blocked] notes...
Ticket H.2 (secrets fail-loudly + opt-out + sweep): [done/blocked] notes...
Ticket H.3 (remove: full tracked-DB cleanup): [done/blocked] notes...
Ticket H.4 (requirements matrix verification): [done/blocked] notes...
Ticket H.5 (enterprise version-mismatch check): [done/blocked] notes...
Deviations from architecture: ...
Open questions for PM: ...
```
