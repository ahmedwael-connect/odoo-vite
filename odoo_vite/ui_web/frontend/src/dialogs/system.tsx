/**
 * System dialogs: Preferences (provisioning mode), About, Import bundle.
 * Ports ui_slint PreferencesDriver / AboutDriver / ImportDriver flows.
 */

import { useEffect, useState } from 'react'
import { getApi } from '../bridge'
import { Modal } from '../components/dialog'
import { ActionButton, DimText, EmptyState, LineList, SectionHeader, Select, TextInput } from '../components/ui'
import { route } from '../events'
import { useApp } from '../store'
import type { AuditEvent, Dict } from '../types'

const MODE_LABELS = ['Developer (default)', 'Managed (least privilege)']
const MODE_NOTES = [
  'Postgres roles get CREATEDB — creating databases just works.',
  'Least-privilege roles — DB create/drop are explicit, privileged operations.',
]
const MODES = ['developer', 'managed']

export function PreferencesDialog({ onClose }: { onClose: () => void }) {
  const api = getApi()
  const [modeIdx, setModeIdx] = useState(0)
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
        <ActionButton primary onClick={onClose}>
          Close
        </ActionButton>
      }
    >
      {events.length === 0 ? (
        <EmptyState text={status || 'No audit events yet.'} />
      ) : (
        <LineList lines={events.map(auditLine)} mono maxHeight={420} />
      )}
      <DimText>App audit stream — refreshes every 2s while open.</DimText>
    </Modal>
  )
}
