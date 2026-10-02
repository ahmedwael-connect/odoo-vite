/**
 * Databases — switch/track/table/ops/schedules.
 * Port of ui_slint/databases.slint + bridge _on_db_action/_on_sched_action.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { getApi } from '../bridge'
import {
  Modal,
  useConfirm,
  useTypedConfirm,
} from '../components/dialog'
import {
  ActionButton,
  Card,
  EmptyState,
  ErrorText,
  SectionHeader,
  Select,
  TextInput,
} from '../components/ui'
import { onEvent, route } from '../events'
import { useApp } from '../store'
import type { DbState, Dict, DiscoverEntry, ScheduleRow } from '../types'

type SortCol = 0 | 1 | 2 | 3

function statusText(st: DbState | undefined, hasStates: boolean): string {
  if (!st) return hasStates ? 'missing' : 'loading…'
  if (st.initialized) return 'initialized'
  if (st.exists) return 'exists, not initialized'
  return hasStates ? 'missing' : 'loading…'
}

// ------------------------------------------------------------------ dialogs

function DiscoverDialog({
  entries,
  version,
  onTrack,
  onClose,
}: {
  entries: DiscoverEntry[]
  version: string
  onTrack: (names: string[]) => void
  onClose: () => void
}) {
  const api = getApi()
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [groups, setGroups] = useState<Record<string, string[]>>({ likely: [], other: [], plain: [] })

  useEffect(() => {
    void api.databases.group_entries(entries as unknown as Dict[], version).then((g) => {
      const gg = g as Record<string, string[]>
      setGroups(gg)
      setPicked(new Set(gg.likely ?? []))
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const toggle = (name: string, on: boolean) =>
    setPicked((prev) => {
      const next = new Set(prev)
      if (on) next.add(name)
      else next.delete(name)
      return next
    })

  const section = (key: string, label: string) => {
    const names = groups[key] ?? []
    if (names.length === 0) return null
    return (
      <div key={key}>
        <SectionHeader text={label} />
        {names.map((n) => (
          <label key={n} className="checkbox">
            <input type="checkbox" checked={picked.has(n)} onChange={(e) => toggle(n, e.target.checked)} />
            <span className="monospace">{n}</span>
          </label>
        ))}
      </div>
    )
  }

  return (
    <Modal
      title="Discover databases"
      onClose={onClose}
      width={520}
      footer={
        <>
          <ActionButton onClick={onClose}>Cancel</ActionButton>
          <ActionButton
            primary
            disabled={picked.size === 0}
            onClick={() => onTrack([...picked])}
          >
            Track {picked.size || ''}
          </ActionButton>
        </>
      }
    >
      <p className="dim-label">Untracked databases found for this instance's server.</p>
      {section('likely', 'Likely (same major version)')}
      {section('other', 'Other versions')}
      {section('plain', 'Uninitialized / unknown')}
      {entries.length === 0 && <EmptyState text="No untracked databases found." />}
    </Modal>
  )
}

function ScheduleDialog({
  dbNames,
  existing,
  onSave,
  onClose,
}: {
  dbNames: string[]
  existing: ScheduleRow | null
  onSave: (payload: {
    databases: string[]
    cron: string
    retention_n: number
    retention_days: number
  }) => void
  onClose: () => void
}) {
  const [picked, setPicked] = useState<Set<string>>(
    new Set(existing?.databases ?? dbNames.slice(0, 1)),
  )
  const [cron, setCron] = useState(existing?.cron ?? '0 3 * * *')
  const [retN, setRetN] = useState(String(existing ? 7 : 7))
  const [retDays, setRetDays] = useState(String(0))

  const ok = picked.size >= 1 && cron.trim().length > 0

  return (
    <Modal
      title={existing ? 'Edit backup schedule' : 'New backup schedule'}
      onClose={onClose}
      width={480}
      footer={
        <>
          <ActionButton onClick={onClose}>Cancel</ActionButton>
          <ActionButton
            primary
            disabled={!ok}
            onClick={() =>
              onSave({
                databases: [...picked],
                cron: cron.trim(),
                retention_n: Number(retN) || 0,
                retention_days: Number(retDays) || 0,
              })
            }
          >
            Save
          </ActionButton>
        </>
      }
    >
      <SectionHeader text="Databases" />
      {dbNames.map((n) => (
        <label key={n} className="checkbox">
          <input
            type="checkbox"
            checked={picked.has(n)}
            onChange={(e) =>
              setPicked((prev) => {
                const next = new Set(prev)
                if (e.target.checked) next.add(n)
                else next.delete(n)
                return next
              })
            }
          />
          <span className="monospace">{n}</span>
        </label>
      ))}
      <label className="field">
        <span className="field-label">Cron expression</span>
        <TextInput value={cron} onChange={(e) => setCron(e.target.value)} placeholder="0 3 * * *" />
      </label>
      <div className="btn-row">
        <label className="field">
          <span className="field-label">Keep N dumps</span>
          <input className="input spin" value={retN} onChange={(e) => setRetN(e.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">Keep N days</span>
          <input className="input spin" value={retDays} onChange={(e) => setRetDays(e.target.value)} />
        </label>
      </div>
      {!ok && <p className="warning">Pick at least one database and a cron expression.</p>}
    </Modal>
  )
}

function FilesDialog({
  files,
  onRestore,
  onDelete,
  onClose,
}: {
  files: { path: string; name: string; detail?: string }[]
  onRestore: (path: string) => void
  onDelete: (path: string) => void
  onClose: () => void
}) {
  const [sel, setSel] = useState('')
  const selected = files.find((f) => f.path === sel)
  return (
    <Modal
      title="Backup files"
      onClose={onClose}
      width={560}
      footer={
        <>
          <ActionButton onClick={onClose}>Close</ActionButton>
          <ActionButton
            danger
            disabled={!selected}
            onClick={() => selected && onDelete(selected.path)}
          >
            Delete
          </ActionButton>
          <ActionButton
            primary
            disabled={!selected}
            onClick={() => selected && onRestore(selected.path)}
          >
            Restore
          </ActionButton>
        </>
      }
    >
      {files.length === 0 ? (
        <EmptyState text="No backup files yet." />
      ) : (
        <div className="sel-list" style={{ maxHeight: 320 }}>
          {files.map((f) => (
            <div
              key={f.path}
              className={`sel-row ${sel === f.path ? 'selected' : ''}`}
              onClick={() => setSel(f.path)}
            >
              <span className="sel-title monospace">{f.name}</span>
              {f.detail && <span className="sel-badge">{f.detail}</span>}
            </div>
          ))}
        </div>
      )}
    </Modal>
  )
}

// -------------------------------------------------------------------- view

export default function Databases() {
  const { current, currentId, setDialog, setBusy } = useApp()
  const confirm = useConfirm()
  const typedConfirm = useTypedConfirm()

  const [states, setStates] = useState<Record<string, DbState>>({})
  const [schedules, setSchedules] = useState<ScheduleRow[]>([])
  const [schedSel, setSchedSel] = useState<string | null>(null)
  const [serverNote, setServerNote] = useState('')
  const [picked, setPicked] = useState('')
  const [manual, setManual] = useState('')
  const [dbSel, setDbSel] = useState('')
  const [sort, setSort] = useState<{ col: SortCol; asc: boolean }>({ col: 0, asc: true })
  const [busy, setLocalBusy] = useState(false)

  const names = useMemo(() => {
    if (!current) return []
    const out = current.primary_db ? [current.primary_db] : []
    for (const d of current.tracked_dbs ?? []) if (!out.includes(d)) out.push(d)
    return out
  }, [current])

  const load = useCallback(async () => {
    if (!currentId) return
    const api = getApi()
    try {
      const reachable = await api.databases.server_reachable()
      setServerNote(
        reachable
          ? ''
          : '⚠ PostgreSQL is unreachable — database operations will fail. Start the server and it clears automatically.',
      )
      void api.databases.refresh_states(currentId)
      void api.databases.refresh_schedules(currentId)
    } catch {
      /* toast already covers facade failures */
    }
  }, [currentId])

  useEffect(() => {
    setStates({})
    setSchedules([])
    setSchedSel(null)
    setDbSel('')
    setPicked('')
    if (!currentId) return
    const offStates = onEvent('db-states', (p) => {
      if (p.instance_id !== currentId) return
      setStates(p.states as Record<string, DbState>)
    })
    const offSched = onEvent('db-schedules', (p) => {
      if (p.instance_id !== currentId) return
      setSchedules(p.schedules as unknown as ScheduleRow[])
      setSchedSel(null)
    })
    const offReport = onEvent('db-report', (p) => {
      if (p.instance_id !== currentId) return
      const checks = ((p.report as { checks?: unknown[] })?.checks ?? []) as {
        ok?: boolean | null
        detail?: string
      }[]
      const failed = checks.filter((c) => c.ok === false)
      const msg =
        `DB config: ${checks.length - failed.length} of ${checks.length} checks passed` +
        (failed.length ? ` — first failure: ${failed[0].detail ?? '?'}` : '')
      route({ kind: 'message', payload: { text: msg, level: failed.length ? 'error' : 'info' } })
    })
    const offDiscover = onEvent('discover-ready', (p) => {
      if (p.instance_id !== currentId) return
      const envelope = p.entries as unknown as {
        ok?: boolean
        message?: string
        entries?: DiscoverEntry[]
      }
      if (!envelope?.ok) {
        route({
          kind: 'message',
          payload: { text: envelope?.message ?? 'Discover failed', level: 'error' },
        })
        return
      }
      setDialog(
        <DiscoverDialog
          entries={envelope.entries ?? []}
          version={current?.version ?? ''}
          onClose={() => setDialog(null)}
          onTrack={(dbNames) => {
            setDialog(null)
            setLocalBusy(true)
            void getApi()
              .databases.track_many(currentId, dbNames)
              .finally(() => {
                setLocalBusy(false)
                void load()
              })
          }}
        />,
      )
    })
    void load()
    return () => {
      offStates()
      offSched()
      offReport()
      offDiscover()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentId, load])

  // keep picked valid
  useEffect(() => {
    if (!picked || names.includes(picked)) return
    setPicked(names[0] ?? '')
  }, [names, picked])

  if (!current || !currentId) {
    return <p className="empty-state dim-label">Select an instance</p>
  }

  const api = getApi()
  const hasStates = Object.keys(states).length > 0

  const run = async (fn: () => Promise<unknown>): Promise<Dict | null> => {
    setLocalBusy(true)
    setBusy(true)
    try {
      const res = (await fn()) as Dict | null
      if (res && res.ok === false) {
        route({ kind: 'message', payload: { text: String(res.message ?? 'failed'), level: 'error' } })
      }
      return res
    } finally {
      setLocalBusy(false)
      setBusy(false)
      void load()
    }
  }

  const requirePicked = (): string | null => {
    if (!picked) {
      route({ kind: 'message', payload: { text: 'Pick a database first', level: 'error' } })
      return null
    }
    return picked
  }

  const onAction = async (action: string) => {
    switch (action) {
      case 'set-primary': {
        const db = requirePicked()
        if (db) await run(() => api.databases.set_primary(currentId, db))
        return
      }
      case 'switch': {
        const db = requirePicked()
        if (!db) return
        const res = await run(() => api.databases.switch_db(currentId, db, false))
        if (!res) return
        const data = res.data as { needs_confirm?: boolean; preview?: { detail?: string } } | undefined
        if (!res.ok && data?.needs_confirm) {
          const ok = await confirm({
            heading: `Switch to "${db}"?`,
            body: `${data.preview?.detail ?? res.message}\n\nThe database will be created first.`,
            confirmLabel: 'Switch',
          })
          if (ok) await run(() => api.databases.switch_db(currentId, db, true))
        }
        return
      }
      case 'track': {
        const name = manual.trim()
        if (!name) {
          route({ kind: 'message', payload: { text: 'Enter a database name', level: 'error' } })
          return
        }
        const res = await run(() => api.databases.track_many(currentId, [name]))
        if (res?.ok) setManual('')
        return
      }
      case 'discover':
        await run(() => api.databases.discover_entries(currentId))
        return
      case 'refresh-states':
        await run(() => api.databases.refresh_states(currentId))
        return
      case 'validate':
        await run(() => api.databases.validate(currentId))
        return
      case 'init-db': {
        const db = requirePicked()
        if (db) await run(() => api.databases.init_db(currentId, db))
        return
      }
      case 'drop-db': {
        const db = requirePicked()
        if (!db) return
        if (db === current.primary_db) {
          route({
            kind: 'message',
            payload: { text: 'Switch primary first, or remove the instance', level: 'error' },
          })
          return
        }
        const ok = await typedConfirm({
          heading: `Drop "${db}" permanently?`,
          body: 'This deletes the database and all of its data. There is no undo.',
          expected: db,
          confirmLabel: 'Drop permanently',
          destructive: true,
        })
        if (ok) await run(() => api.databases.drop_db(currentId, db))
        return
      }
      case 'backup-db': {
        const db = requirePicked()
        if (!db) return
        const dest = await api.app.pick_file(
          'Backup database as…',
          'save',
          'Postgres dumps (*.dump)',
        )
        if (!dest.ok || !dest.path) return
        await run(() => api.databases.backup_db(currentId, db, dest.path!))
        return
      }
      case 'restore-db': {
        const db = requirePicked()
        if (!db) return
        const dump = await api.app.pick_file(
          'Restore database from…',
          'open',
          'Postgres dumps (*.dump *.sql *.sql.gz)',
        )
        if (!dump.ok || !dump.path) return
        const ok = await typedConfirm({
          heading: 'Restore database?',
          body: `Drops and recreates "${db}" from the dump.\nRe-type the database name to confirm.`,
          expected: db,
          confirmLabel: 'Restore (drop + recreate)',
          destructive: true,
        })
        if (ok) await run(() => api.databases.restore_db(currentId, dump.path!, db))
        return
      }
    }
  }

  const onSchedAction = async (action: string) => {
    const sel = schedules.find((s) => s.id === schedSel) ?? null
    if (action === 'add') {
      if (names.length === 0) {
        route({ kind: 'message', payload: { text: 'Track a database first', level: 'error' } })
        return
      }
      setDialog(
        <ScheduleDialog
          dbNames={names}
          existing={null}
          onClose={() => setDialog(null)}
          onSave={(payload) => {
            setDialog(null)
            void run(() => api.databases.sched_create(currentId, payload))
          }}
        />,
      )
      return
    }
    if (!sel) {
      route({ kind: 'message', payload: { text: 'Pick a schedule first', level: 'error' } })
      return
    }
    if (action === 'edit') {
      setDialog(
        <ScheduleDialog
          dbNames={names}
          existing={sel}
          onClose={() => setDialog(null)}
          onSave={(payload) => {
            setDialog(null)
            void run(() => api.databases.sched_update(sel.id, payload))
          }}
        />,
      )
    } else if (action === 'run') {
      await run(() => api.databases.run_schedule_now(sel.id))
    } else if (action === 'toggle') {
      await run(() => api.databases.sched_toggle(sel.id, !sel.enabled))
    } else if (action === 'delete') {
      const ok = await confirm({
        heading: `Delete schedule ${sel.cron}?`,
        body: 'Scheduled backups stop; existing dumps stay on disk.',
        confirmLabel: 'Delete',
        destructive: true,
      })
      if (ok) await run(() => api.databases.sched_delete(sel.id))
    } else if (action === 'files') {
      const files = (await api.databases.list_backup_files(currentId)) as {
        path: string
        name: string
        detail?: string
      }[]
      setDialog(
        <FilesDialog
          files={files}
          onClose={() => setDialog(null)}
          onRestore={(path) => {
            setDialog(null)
            void (async () => {
              const ok = await typedConfirm({
                heading: 'Restore from file?',
                body: `Drops and recreates "${picked}" from:\n${path}`,
                expected: picked,
                confirmLabel: 'Restore (drop + recreate)',
                destructive: true,
              })
              if (ok) await run(() => api.databases.restore_db(currentId, path, picked))
            })()
          }}
          onDelete={(path) => {
            setDialog(null)
            void (async () => {
              const ok = await confirm({
                heading: 'Delete dump file?',
                body: path,
                confirmLabel: 'Delete',
                destructive: true,
              })
              if (ok) await run(() => api.databases.file_delete(path))
            })()
          }}
        />,
      )
    }
  }

  // ---- table rows
  const rowFor = (db: string) => {
    const st = states[db]
    return {
      db,
      isPrimary: db === current.primary_db,
      size: st?.size ?? (st?.size_bytes ? `${Math.round(st.size_bytes / 1024)} KB` : '—'),
      sizeBytes: st?.size_bytes ?? -1,
      version: st?.odoo_version ? `v${st.odoo_version}` : '—',
      versionKey: st?.odoo_version ?? '',
      status: statusText(st, hasStates),
      st,
    }
  }
  const statusRank = (s: string) =>
    s === 'initialized' ? 0 : s === 'exists, not initialized' ? 1 : s === 'loading…' ? 3 : 2

  const rows = names.map(rowFor).sort((a, b) => {
    if (a.isPrimary) return -1
    if (b.isPrimary) return 1
    const { col, asc } = sort
    let d = 0
    if (col === 0) d = a.db.localeCompare(b.db)
    else if (col === 1) d = a.sizeBytes - b.sizeBytes
    else if (col === 2) d = a.versionKey.localeCompare(b.versionKey)
    else d = statusRank(a.status) - statusRank(b.status)
    return asc ? d : -d
  })

  const schedSelRow = schedules.find((s) => s.id === schedSel)

  const header = (col: SortCol, label: string) => (
    <th
      key={col}
      onClick={() => setSort((s) => ({ col, asc: s.col === col ? !s.asc : true }))}
      className="sortable"
    >
      {label}
      {sort.col === col ? (sort.asc ? ' ▲' : ' ▼') : ''}
    </th>
  )

  return (
    <div className="view">
      <ErrorText text={serverNote.replace('⚠ ', '')} />

      <Card title="Switch database">
        <div className="btn-row">
          <Select value={picked} onChange={(e) => setPicked(e.target.value)}>
            <option value="">— pick a database —</option>
            {names.map((n) => (
              <option key={n} value={n}>
                {n}
                {n === current.primary_db ? ' ★' : ''}
              </option>
            ))}
          </Select>
          <ActionButton disabled={busy} onClick={() => void onAction('set-primary')}>
            Set as Primary
          </ActionButton>
          <ActionButton disabled={busy} onClick={() => void onAction('switch')}>
            Switch Now
          </ActionButton>
        </div>
        <div className="btn-row">
          <span className="field-label">Database name:</span>
          <TextInput
            placeholder="Add by name…"
            value={manual}
            onChange={(e) => setManual(e.target.value)}
          />
          <ActionButton disabled={busy} onClick={() => void onAction('track')}>
            Track
          </ActionButton>
        </div>
      </Card>

      <Card title="Tracked databases">
        <table className="table">
          <thead>
            <tr>
              {header(0, 'Database')}
              {header(1, 'Size')}
              {header(2, 'Version')}
              {header(3, 'Status')}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr
                key={r.db}
                className={dbSel === r.db ? 'discover-picked' : ''}
                onClick={() => {
                  setDbSel(r.db)
                  setPicked(r.db)
                }}
              >
                <td className="monospace">
                  {r.isPrimary ? '★ ' : ''}
                  {r.db}
                </td>
                <td>{r.size}</td>
                <td>{r.version}</td>
                <td>{r.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {names.length === 0 && <EmptyState text="No databases tracked yet." />}

        <SectionHeader text="Inspect" />
        <div className="btn-row">
          <ActionButton disabled={busy} onClick={() => void onAction('discover')}>
            Discover
          </ActionButton>
          <ActionButton disabled={busy} onClick={() => void onAction('refresh-states')}>
            Refresh states
          </ActionButton>
          <ActionButton disabled={busy} onClick={() => void onAction('validate')}>
            Validate
          </ActionButton>
        </div>

        <SectionHeader text="Danger zone" />
        <div className="btn-row">
          <ActionButton disabled={busy} onClick={() => void onAction('init-db')}>
            Init
          </ActionButton>
          <ActionButton
            danger
            disabled={busy}
            title="Permanently deletes the database — never merged"
            onClick={() => void onAction('drop-db')}
          >
            Drop
          </ActionButton>
        </div>

        <SectionHeader text="Transfer" />
        <div className="btn-row">
          <ActionButton disabled={busy} onClick={() => void onAction('backup-db')}>
            Backup
          </ActionButton>
          <ActionButton
            disabled={busy}
            title="Drops and recreates the target database"
            onClick={() => void onAction('restore-db')}
          >
            Restore
          </ActionButton>
        </div>
      </Card>

      <Card title="Backup schedules">
        <div className="sel-list" style={{ maxHeight: 200 }}>
          {schedules.map((s) => (
            <div
              key={s.id}
              className={`sel-row ${schedSel === s.id ? 'selected' : ''}`}
              onClick={() => setSchedSel(s.id)}
            >
              <span className="sel-title">
                {s.enabled ? '✓' : '✗'} {s.cron} · {s.enabled ? 'on' : 'off'}
              </span>
              <span className="sel-badge dim-label">
                {(s.databases ?? []).join(', ')} · last {s.last_run || 'never'}{' '}
                {s.last_status ?? ''}
              </span>
            </div>
          ))}
          {schedules.length === 0 && <EmptyState text="No schedules yet — press Add." />}
        </div>
        <div className="btn-row">
          <ActionButton disabled={busy} onClick={() => void onSchedAction('add')}>
            Add
          </ActionButton>
          <ActionButton disabled={busy} onClick={() => void onSchedAction('edit')}>
            Edit
          </ActionButton>
          <ActionButton danger disabled={busy} onClick={() => void onSchedAction('delete')}>
            Delete
          </ActionButton>
        </div>
        <div className="btn-row">
          <ActionButton disabled={busy} onClick={() => void onSchedAction('run')}>
            Run Now
          </ActionButton>
          <ActionButton disabled={busy || !schedSelRow} onClick={() => void onSchedAction('toggle')}>
            Toggle
          </ActionButton>
          <ActionButton disabled={busy} onClick={() => void onSchedAction('files')}>
            Files
          </ActionButton>
        </div>
      </Card>
    </div>
  )
}
