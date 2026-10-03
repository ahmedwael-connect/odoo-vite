/**
 * System dialogs: Preferences (provisioning mode), About, Import bundle.
 * Ports ui_slint PreferencesDriver / AboutDriver / ImportDriver flows.
 */

import { useEffect, useState } from 'react'
import { copyWithToast, downloadText } from '../clipboard'
import { getApi } from '../bridge'
import { Modal } from '../components/dialog'
import { ActionButton, DimText, EmptyState, LineList, SectionHeader, Select, StatusPill, TextInput } from '../components/ui'
import { route, type ProgressOp } from '../events'
import { useApp } from '../store'
import { getTheme, setTheme, type ThemeChoice } from '../theme'
import type { AuditEvent, Dict } from '../types'

const MODE_LABELS = ['Developer (default)', 'Managed (least privilege)']
const MODE_NOTES = [
  'Postgres roles get CREATEDB — creating databases just works.',
  'Least-privilege roles — DB create/drop are explicit, privileged operations.',
]
const MODES = ['developer', 'managed']

const THEME_LABELS: Record<ThemeChoice, string> = {
  system: 'System (follow OS)',
  dark: 'Dark',
  light: 'Light',
}
const THEMES: ThemeChoice[] = ['system', 'dark', 'light']

export function PreferencesDialog({ onClose }: { onClose: () => void }) {
  const api = getApi()
  const [modeIdx, setModeIdx] = useState(0)
  const [theme, setThemeChoice] = useState<ThemeChoice>(() => getTheme())
  const [keyring, setKeyring] = useState('')
  const [dbPath, setDbPath] = useState('')
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    void api.app.preferences().then((prefs) => {
      const mode = String(prefs.mode ?? 'developer')
      setModeIdx(MODES.indexOf(mode) >= 0 ? MODES.indexOf(mode) : 0)
      setKeyring(String(prefs.keyring_text ?? ''))
      setDbPath(String(prefs.db_path ?? ''))
      setLoaded(true)
    })
  }, [api])

  const save = async () => {
    const res = await api.app.save_preferences(MODES[modeIdx])
    route({
      kind: 'message',
      payload: {
        text: res.ok ? res.message : `Could not save preferences: ${res.message}`,
        level: res.ok ? 'info' : 'error',
      },
    })
    onClose()
  }

  return (
    <Modal
      title="Preferences"
      onClose={onClose}
      width={520}
      footer={
        <>
          <ActionButton onClick={onClose}>Cancel</ActionButton>
          <ActionButton primary onClick={() => void save()}>
            Save
          </ActionButton>
        </>
      }
    >
      <SectionHeader text="Appearance" />
      <Select
        aria-label="Theme"
        value={theme}
        onChange={(e) => {
          const next = e.target.value as ThemeChoice
          setThemeChoice(next)
          setTheme(next) // instant preview + persist
        }}
      >
        {THEMES.map((t) => (
          <option key={t} value={t}>
            {THEME_LABELS[t]}
          </option>
        ))}
      </Select>
      <SectionHeader text="Provisioning mode (applies to new instances)" />
      <Select value={String(modeIdx)} onChange={(e) => setModeIdx(Number(e.target.value))}>
        {MODE_LABELS.map((l, i) => (
          <option key={l} value={i}>
            {l}
          </option>
        ))}
      </Select>
      <DimText>{MODE_NOTES[modeIdx]}</DimText>
      <DimText>{keyring}</DimText>
      {dbPath && <EmptyState text={`Registry: ${dbPath}`} />}
      {!loaded && <DimText>loading…</DimText>}
    </Modal>
  )
}

export function AboutDialog({ onClose }: { onClose: () => void }) {
  const [version, setVersion] = useState('…')
  useEffect(() => {
    void getApi()
      .app.version()
      .then(setVersion)
      .catch(() => setVersion('?'))
  }, [])

  return (
    <Modal
      title="About"
      onClose={onClose}
      width={480}
      footer={
        <ActionButton primary onClick={onClose}>
          Close
        </ActionButton>
      }
    >
      <SectionHeader text={`Odoo Vite ${version} — local Odoo instance manager`} />
      <DimText>
        Web UI built with React + TypeScript on pywebview. All instance, database, module and
        transfer operations run locally through the bundled core.
      </DimText>
      <DimText>No network calls except the Odoo instances you run yourself.</DimText>
    </Modal>
  )
}

/**
 * Import bundle: pick a .tar.gz, preview its manifest, confirm the new
 * name/port, then import. (Slint _continue_import/_done_import parity.)
 */
export function ImportDialog({ onClose }: { onClose: () => void }) {
  const api = getApi()
  const { refresh } = useApp()
  const [archive, setArchive] = useState('')
  const [info, setInfo] = useState<Dict | null>(null)
  const [name, setName] = useState('')
  const [port, setPort] = useState(8070)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const pick = async () => {
    const res = await api.app.pick_file('Import bundle archive', 'open', 'tar.gz')
    if (!res.ok || !res.path) return
    const preview = await api.transfer.preview(res.path)
    if (!preview.ok) {
      setError(preview.message)
      return
    }
    const data = (preview.data ?? {}) as Dict
    setArchive(res.path)
    setInfo(data)
    setName(String(data.name ?? ''))
    let suggested = 8070
    try {
      suggested = await api.app.suggest_port(Number(data.port ?? 8069) + 1)
    } catch {
      suggested = 8070
    }
    setPort(suggested)
    setError('')
  }

  const run = async () => {
    const trimmed = name.trim()
    if (!trimmed) {
      setError('Enter a name for the imported instance.')
      return
    }
    setBusy(true)
    try {
      const res = await api.transfer.import_bundle(archive, trimmed, port)
      if (!res.ok) {
        setError(res.message)
        return
      }
      refresh()
      route({ kind: 'message', payload: { text: res.message, level: 'info' } })
      onClose()
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      title="Import bundle"
      onClose={onClose}
      width={520}
      footer={
        <>
          <ActionButton onClick={onClose}>Cancel</ActionButton>
          <ActionButton primary disabled={busy || !archive} onClick={() => void run()}>
            Import
          </ActionButton>
        </>
      }
    >
      <div className="btn-row">
        <TextInput placeholder="bundle .tar.gz" value={archive} readOnly onChange={() => undefined} />
        <ActionButton onClick={() => void pick()}>Browse…</ActionButton>
      </div>
      {info && (
        <>
          <SectionHeader text="Bundle contents" />
          <DimText>
            {String(info.name ?? '?')} — Odoo {String(info.version ?? '?')}
          </DimText>
        </>
      )}
      <div className="grid2">
        <label className="field">
          <span className="field-label">New instance name</span>
          <TextInput value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">Port</span>
          <TextInput value={String(port)} onChange={(e) => setPort(Number(e.target.value) || 0)} />
        </label>
      </div>
      {error && <p className="error">{error}</p>}
    </Modal>
  )
}

/**
 * Event log — live tail of the app audit stream (Sprint 8 ticket B.6;
 * the Qt event dock, restored after the Slint drop). Polls
 * `audit.tail` every 2s while open; newest last.
 */
const auditLine = (e: AuditEvent): string => {
  let t = ''
  try {
    t = e.ts ? new Date(e.ts).toLocaleTimeString() : ''
  } catch {
    t = String(e.ts ?? '')
  }
  const who = e.instance_name || e.instance_id || '?'
  return `${t}  ${(e.action || '?').padEnd(10)}  ${who}${e.detail ? ` — ${e.detail}` : ''}`
}

export function EventLogDialog({ onClose }: { onClose: () => void }) {
  const api = getApi()
  const [events, setEvents] = useState<AuditEvent[]>([])
  const [status, setStatus] = useState('Loading…')
  const [q, setQ] = useState('')

  const filtered = q.trim()
    ? events.filter((e) => auditLine(e).toLowerCase().includes(q.trim().toLowerCase()))
    : events

  const asJson = () => JSON.stringify(filtered, null, 2)
  const asCsv = () => {
    const esc = (v: unknown) => {
      const s = String(v ?? '')
      return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
    }
    const head = 'ts,instance_id,instance_name,action,detail'
    const rows = filtered.map((e) =>
      [e.ts, e.instance_id, e.instance_name, e.action, e.detail ?? ''].map(esc).join(','),
    )
    return [head, ...rows].join('\n')
  }

  useEffect(() => {
    let stopped = false
    const tick = async () => {
      try {
        const rows = await api.audit.tail(300)
        if (stopped) return
        const list = Array.isArray(rows) ? rows : []
        setEvents(list)
        setStatus('')
      } catch {
        if (!stopped) setStatus('Cannot read the audit log.')
      }
    }
    void tick()
    const timer = window.setInterval(() => void tick(), 2000)
    return () => {
      stopped = true
      window.clearInterval(timer)
    }
  }, [api])

  return (
    <Modal
      title="Event log"
      width={720}
      onClose={onClose}
      footer={
        <>
          <ActionButton
            disabled={!filtered.length}
            title="Copy the filtered events as text"
            onClick={() => void copyWithToast(filtered.map(auditLine).join('\n'), `${filtered.length} events`)}
          >
            Copy
          </ActionButton>
          <ActionButton
            disabled={!filtered.length}
            title="Download the filtered events as JSON"
            onClick={() => downloadText('odoo-vite-events.json', 'application/json', asJson())}
          >
            JSON
          </ActionButton>
          <ActionButton
            disabled={!filtered.length}
            title="Download the filtered events as CSV"
            onClick={() => downloadText('odoo-vite-events.csv', 'text/csv', asCsv())}
          >
            CSV
          </ActionButton>
          <ActionButton primary onClick={onClose}>
            Close
          </ActionButton>
        </>
      }
    >
      <TextInput
        placeholder="Filter events…"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        aria-label="Filter events"
      />
      {events.length > 0 && (
        <DimText>
          {filtered.length} of {events.length} events
        </DimText>
      )}
      {filtered.length === 0 ? (
        <EmptyState text={status || (events.length ? 'No events match the filter.' : 'No audit events yet.')} />
      ) : (
        <LineList lines={filtered.map(auditLine)} mono maxHeight={420} />
      )}
      <DimText>App audit stream — refreshes every 2s while open.</DimText>
    </Modal>
  )
}

/**
 * Live operations feed (3.1.0 C1): every progress-streaming operation,
 * newest first, with the last line inline and full output on expand.
 * Fed by the same useProgress() the statusline counter uses.
 */
export function ActivityDialog({ ops, onClose }: { ops: ProgressOp[]; onClose: () => void }) {
  const [expanded, setExpanded] = useState<string | null>(null)
  const sorted = [...ops].sort((a, b) => b.startedAt - a.startedAt)
  const time = (t: number) => {
    try {
      return new Date(t).toLocaleTimeString()
    } catch {
      return ''
    }
  }
  const title = (op: ProgressOp) =>
    op.lines[0] ? `${op.lines[0]}`.slice(0, 160) : op.op_id
  const asText = () =>
    sorted
      .map((op) =>
        [
          `${time(op.startedAt)} ${op.op_id} ${op.done ? 'done' : 'running'}`,
          ...op.lines,
        ].join('\n'),
      )
      .join('\n\n')

  return (
    <Modal
      title="Live operations"
      width={680}
      onClose={onClose}
      footer={
        <>
          <ActionButton
            disabled={!sorted.length}
            title="Copy the whole feed to the clipboard"
            onClick={() => void copyWithToast(asText(), `${sorted.length} operations`)}
          >
            Copy
          </ActionButton>
          <ActionButton primary onClick={onClose}>
            Close
          </ActionButton>
        </>
      }
    >
      {sorted.length === 0 ? (
        <EmptyState text="No operations yet — progress streams here while they run." />
      ) : (
        <div className="sel-list" style={{ maxHeight: 420 }}>
          {sorted.map((op) => (
            <div
              key={op.op_id}
              className={`sel-row wrap ${expanded === op.op_id ? 'selected' : ''}`}
              onClick={() => setExpanded(expanded === op.op_id ? null : op.op_id)}
              title={op.op_id}
            >
              <span className="sel-title">
                <StatusPill status={op.done ? 'done' : 'running'} /> {title(op)}
              </span>
              <span className="sel-badge dim-label">
                {time(op.startedAt)} · {op.lines.length} line
                {op.lines.length === 1 ? '' : 's'} · {op.done ? 'finished' : 'live'}
              </span>
              {expanded === op.op_id && (
                <div
                  className="sel-expand"
                  onClick={(e) => e.stopPropagation()}
                >
                  <DimText>{op.op_id}</DimText>
                  {op.lines.length ? (
                    <LineList lines={op.lines} mono maxHeight={200} />
                  ) : (
                    <DimText>No progress lines yet.</DimText>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      <DimText>Streaming output from every progress operation — click a row to expand.</DimText>
    </Modal>
  )
}
