/**
 * Shell — sidebar (instance list + System), header (name/status/actions),
 * six tabs, status line, toast snackbar, dialog host.
 * Port of ui_slint/app.slint + bridge refresh/select/show_toast.
 */

import { useEffect, useState } from 'react'
import { getApi, useBridgeReady } from './bridge'
import { useConfirm, useProgressRun } from './components/dialog'
import { SelectionList, type SelRow } from './components/selection'
import { ActionButton, SectionHeader, Spinner, StatusPill } from './components/ui'
import { route, useProgress } from './events'
import { AboutDialog, EventLogDialog, ImportDialog, PreferencesDialog } from './dialogs/system'
import { AdoptWizard, CreateWizard } from './dialogs/wizards'
import { useApp } from './store'
import Configuration from './views/Configuration'
import Databases from './views/Databases'
import DevTools from './views/DevTools'
import Logs from './views/Logs'
import Modules from './views/Modules'
import Overview from './views/Overview'

type Tab = 'overview' | 'databases' | 'modules' | 'configuration' | 'logs' | 'devtools'

const TABS: { id: Tab; label: string }[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'databases', label: 'Databases' },
  { id: 'modules', label: 'Modules' },
  { id: 'configuration', label: 'Configuration' },
  { id: 'logs', label: 'Logs' },
  { id: 'devtools', label: 'DevTools' },
]

const cap = (s: string) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : '')

export default function App() {
  const { ready, native } = useBridgeReady()
  const {
    statuses,
    currentId,
    current,
    select,
    refresh,
    toast,
    hideToast,
    busy,
    setBusy,
    dialog,
    setDialog,
  } = useApp()
  const ops = useProgress()
  const confirm = useConfirm()
  const runProgress = useProgressRun()
  const api = getApi()
  const [tab, setTab] = useState<Tab>('overview')
  const [bulkMode, setBulkMode] = useState(false)
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set())

  const activeOps = ops.filter((o) => !o.done).length

  useEffect(() => {
    document.title = current ? `Odoo Vite — ${current.name}` : 'Odoo Vite'
  }, [current])

  // App shortcuts (RM-6): Ctrl+N New, Ctrl+O Adopt, F5/Ctrl+R Refresh,
  // Ctrl+F focus the instance filter. Nothing fires while a dialog is
  // open except Refresh — Esc owns dialog dismissal.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented) return
      const mod = e.ctrlKey || e.metaKey
      const key = e.key.toLowerCase()
      if (key === 'f5' || (mod && key === 'r')) {
        e.preventDefault()
        if (!busy && !dialog) void refresh()
        return
      }
      if (!mod) return
      if (key === 'n' && !dialog) {
        e.preventDefault()
        setDialog(<CreateWizard onClose={() => setDialog(null)} />)
      } else if (key === 'o' && !dialog) {
        e.preventDefault()
        setDialog(<AdoptWizard onClose={() => setDialog(null)} />)
      } else if (key === 'f') {
        e.preventDefault()
        document
          .querySelector<HTMLInputElement>('.sidebar .sel-list-wrap input')
          ?.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [busy, dialog, refresh, setDialog])

  const rows: SelRow[] = statuses.map((s) => ({
    id: s.id,
    title: s.name,
    badge: cap(s.status),
    checked: checkedIds.has(s.id),
  }))

  // drop ghosts (removed instances) from the checked set at use time
  const selectedIds = [...checkedIds].filter((id) =>
    statuses.some((s) => s.id === id),
  )

  const toggleChecked = (id: string, on: boolean) =>
    setCheckedIds((prev) => {
      const next = new Set(prev)
      if (on) next.add(id)
      else next.delete(id)
      return next
    })

  const runBulk = async (action: 'start_many' | 'stop_many') => {
    const ids = selectedIds
    if (ids.length === 0 || busy) return
    if (action === 'stop_many') {
      const names = ids.map((id) => statuses.find((s) => s.id === id)?.name ?? id)
      const ok = await confirm({
        heading: `Stop ${ids.length} instance(s)?`,
        body: names.join(', '),
        confirmLabel: 'Stop',
        destructive: true,
      })
      if (!ok) return
    }
    setBusy(true)
    try {
      const title =
        action === 'start_many'
          ? `Starting ${ids.length} instance(s)`
          : `Stopping ${ids.length} instance(s)`
      const res = await runProgress(title, (opId) =>
        action === 'start_many'
          ? api.lifecycle.start_many(ids, opId)
          : api.lifecycle.stop_many(ids, opId),
      )
      route({ kind: 'message', payload: { text: res.message, level: res.ok ? 'info' : 'error' } })
      if (res.ok) setCheckedIds(new Set())
      await refresh()
    } finally {
      setBusy(false)
    }
  }

  const statusRow = currentId ? statuses.find((s) => s.id === currentId) : undefined
  const statusLine = `${statuses.length} instance(s) — ${
    activeOps > 0 ? `${activeOps} operation(s) running` : 'Ready'
  }`

  const openDialog = (node: React.ReactNode) => setDialog(node)

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="sidebar-title">Instances</div>
        {statuses.length > 0 && (
          <div className="btn-row">
            <ActionButton
              disabled={busy}
              onClick={() => {
                setBulkMode((b) => !b)
                setCheckedIds(new Set())
              }}
            >
              {bulkMode ? 'Done selecting' : 'Bulk select…'}
            </ActionButton>
            {bulkMode && selectedIds.length > 0 && (
              <ActionButton onClick={() => setCheckedIds(new Set())}>Clear</ActionButton>
            )}
          </div>
        )}
        <SelectionList
          rows={rows}
          selectedId={currentId ?? undefined}
          onPick={select}
          multi={bulkMode}
          onToggle={toggleChecked}
          showFilter
          counts={
            statuses.length
              ? bulkMode && selectedIds.length
                ? `${selectedIds.length} of ${statuses.length} selected`
                : `${statuses.length} instance(s)`
              : ''
          }
          emptyState={
            <p className="empty-state dim-label">
              No instances yet — create or adopt one to begin.
            </p>
          }
        />
        {bulkMode && selectedIds.length > 0 && (
          <div className="btn-row">
            <ActionButton
              primary
              disabled={busy}
              title="Start the selected instances sequentially (one worker)"
              onClick={() => void runBulk('start_many')}
            >
              Start ({selectedIds.length})
            </ActionButton>
            <ActionButton
              danger
              disabled={busy}
              title="Stop the selected instances sequentially (one worker)"
              onClick={() => void runBulk('stop_many')}
            >
              Stop ({selectedIds.length})
            </ActionButton>
          </div>
        )}
        <div className="sidebar-divider" />
        <SectionHeader text="System" />
        <div className="btn-row">
          <ActionButton onClick={() => openDialog(<PreferencesDialog onClose={() => setDialog(null)} />)}>
            Preferences
          </ActionButton>
          <ActionButton onClick={() => openDialog(<ImportDialog onClose={() => setDialog(null)} />)}>
            Import…
          </ActionButton>
        </div>
        <ActionButton
          title="Live audit events (start/stop/… across all instances)"
          onClick={() => openDialog(<EventLogDialog onClose={() => setDialog(null)} />)}
        >
          Event log
        </ActionButton>
        <ActionButton onClick={() => openDialog(<AboutDialog onClose={() => setDialog(null)} />)}>
          About
        </ActionButton>
      </aside>

      <div className="main-col">
        <header className="app-header">
          <span className="app-title">{current?.name ?? 'Odoo Vite'}</span>
          {statusRow && <StatusPill status={statusRow.status} />}
          <span className="spacer" />
          <ActionButton onClick={() => openDialog(<CreateWizard onClose={() => setDialog(null)} />)}>
            New…
          </ActionButton>
          <ActionButton onClick={() => openDialog(<AdoptWizard onClose={() => setDialog(null)} />)}>
            Adopt…
          </ActionButton>
          <ActionButton disabled={busy} onClick={refresh}>
            Refresh
          </ActionButton>
        </header>

        <nav className="tabbar">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              className={tab === t.id ? 'tab active' : 'tab'}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </nav>

        <main className="content">
          {tab === 'overview' && <Overview />}
          {tab === 'databases' && <Databases />}
          {tab === 'modules' && <Modules />}
          {tab === 'configuration' && <Configuration />}
          {tab === 'logs' && <Logs />}
          {tab === 'devtools' && <DevTools />}
        </main>

        <footer className="statusline">
          <span className="dim-label">{statusLine}</span>
          {busy && <Spinner />}
          <span className="spacer" />
          <span className={`chip ${ready ? 'on' : 'off'}`}>
            {ready ? (native ? 'bridge ready' : 'browser dev') : 'connecting…'}
          </span>
        </footer>
      </div>

      {toast && (
        <div
          className={`snackbar toast-${toast.level}`}
          role="status"
          onClick={hideToast}
        >
          {toast.text}
        </div>
      )}

      {dialog}
    </div>
  )
}
