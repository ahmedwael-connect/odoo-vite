/**
 * Shell — sidebar (instance list + System), header (name/status/actions),
 * six tabs, status line, toast snackbar, dialog host.
 * Port of ui_slint/app.slint + bridge refresh/select/show_toast.
 */

import { useEffect, useState } from 'react'
import { useBridgeReady } from './bridge'
import { SelectionList, type SelRow } from './components/selection'
import { ActionButton, SectionHeader, Spinner, StatusPill } from './components/ui'
import { useProgress } from './events'
import { AboutDialog, ImportDialog, PreferencesDialog } from './dialogs/system'
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
    dialog,
    setDialog,
  } = useApp()
  const ops = useProgress()
  const [tab, setTab] = useState<Tab>('overview')

  const activeOps = ops.filter((o) => !o.done).length

  useEffect(() => {
    document.title = current ? `Odoo Vite — ${current.name}` : 'Odoo Vite'
  }, [current])

  const rows: SelRow[] = statuses.map((s) => ({
    id: s.id,
    title: s.name,
    badge: cap(s.status),
  }))

  const statusRow = currentId ? statuses.find((s) => s.id === currentId) : undefined
  const statusLine = `${statuses.length} instance(s) — ${
    activeOps > 0 ? `${activeOps} operation(s) running` : 'Ready'
  }`

  const openDialog = (node: React.ReactNode) => setDialog(node)

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="sidebar-title">Instances</div>
        <SelectionList
          rows={rows}
          selectedId={currentId ?? undefined}
          onPick={select}
          showFilter
          counts={statuses.length ? `${statuses.length} instance(s)` : ''}
          emptyState={
            <p className="empty-state dim-label">
              No instances yet — create or adopt one to begin.
            </p>
          }
        />
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
