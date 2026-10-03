/**
 * Shell — sidebar (instance list + System), header (name/status/actions),
 * seven tabs, status line, toast snackbar, dialog host.
 * Port of ui_slint/app.slint + bridge refresh/select/show_toast.
 */

import { Fragment, useEffect, useState } from 'react'
import { getApi, useBridgeReady } from './bridge'
import { useConfirm, useProgressRun } from './components/dialog'
import { Icon } from './components/icons'
import { CommandPalette, type Command } from './components/palette'
import { SelectionList, type SelRow } from './components/selection'
import { ActionButton, SectionHeader, Spinner, StatusPill } from './components/ui'
import { route, useProgress } from './events'
import { AboutDialog, EventLogDialog, ImportDialog, PreferencesDialog } from './dialogs/system'
import { IndexModuleDialog } from './dialogs/marketplace'
import { AdoptWizard, CreateWizard } from './dialogs/wizards'
import { useApp } from './store'
import { getTheme, initTheme, resolveTheme, setTheme } from './theme'
import Configuration from './views/Configuration'
import Databases from './views/Databases'
import DevTools from './views/DevTools'
import Logs from './views/Logs'
import Marketplace from './views/Marketplace'
import Modules from './views/Modules'
import Overview from './views/Overview'

type Tab =
  | 'overview'
  | 'databases'
  | 'modules'
  | 'marketplace'
  | 'configuration'
  | 'logs'
  | 'devtools'

const TABS: { id: Tab; label: string }[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'databases', label: 'Databases' },
  { id: 'modules', label: 'Modules' },
  { id: 'marketplace', label: 'Marketplace' },
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
    toasts,
    hideToast,
    busy,
    setBusy,
    dialogs,
    setDialog,
  } = useApp()
  const ops = useProgress()
  const confirm = useConfirm()
  const runProgress = useProgressRun()
  const api = getApi()
  const [tab, setTab] = useState<Tab>('overview')
  const [bulkMode, setBulkMode] = useState(false)
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set())
  const [paletteOpen, setPaletteOpen] = useState(false)

  const activeOps = ops.filter((o) => !o.done).length

  useEffect(() => {
    document.title = current ? `Odoo Vite — ${current.name}` : 'Odoo Vite'
  }, [current])

  // saved theme (dark/light/system) on <html data-theme>; follows OS live
  useEffect(() => initTheme(), [])

  // App shortcuts (RM-6): Ctrl+N New, Ctrl+O Adopt, F5/Ctrl+R Refresh,
  // Ctrl+F focus the instance filter, Ctrl+K command palette. Nothing
  // fires while a dialog is open except Refresh — Esc owns dismissal.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented) return
      const mod = e.ctrlKey || e.metaKey
      const key = e.key.toLowerCase()
      const dialogOpen = dialogs.length > 0
      if (key === 'f5' || (mod && key === 'r')) {
        e.preventDefault()
        if (!busy && !dialogOpen) void refresh()
        return
      }
      if (!mod) return
      if (key === 'k') {
        e.preventDefault()
        if (!dialogOpen) setPaletteOpen((open) => !open)
      } else if (key === 'n' && !dialogOpen) {
        e.preventDefault()
        setDialog(<CreateWizard onClose={() => setDialog(null)} />)
      } else if (key === 'o' && !dialogOpen) {
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
  }, [busy, dialogs, refresh, setDialog])

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

  // lifecycle helpers for palette/shortcuts (mirror Overview's act flow)
  const lifecycleAct = async (
    fn: () => Promise<{ ok: boolean; message: string; data?: unknown }>,
  ) => {
    setBusy(true)
    try {
      return await fn()
    } catch (err) {
      return { ok: false, message: err instanceof Error ? err.message : String(err) }
    } finally {
      setBusy(false)
      void refresh()
    }
  }

  const startCurrent = async () => {
    if (busy || !currentId) return
    const res = await lifecycleAct(() => api.lifecycle.start(currentId, null, false))
    if (res.ok) return
    const data = res.data as { needs_confirm?: boolean; preview?: { detail?: string } } | undefined
    if (data?.needs_confirm) {
      const ok = await confirm({
        heading: 'Create database?',
        body: `${data.preview?.detail ?? res.message}\n\nRuns odoo-bin -i base, then starts.`,
        confirmLabel: 'Create + Start',
      })
      if (ok) await lifecycleAct(() => api.lifecycle.start(currentId, null, true))
    } else {
      route({ kind: 'message', payload: { text: res.message, level: 'error' } })
    }
  }

  const stopCurrent = async () => {
    if (busy || !currentId) return
    const res = await lifecycleAct(() => api.lifecycle.stop(currentId))
    if (!res.ok)
      route({ kind: 'message', payload: { text: res.message, level: 'error' } })
  }

  const commands: Command[] = [
    ...TABS.map((t) => ({
      id: `go-${t.id}`,
      label: `Go to ${t.label}`,
      group: 'Navigate',
      icon: 'chevron-right' as const,
      run: () => setTab(t.id),
    })),
    {
      id: 'new-instance',
      label: 'New instance…',
      group: 'Instance',
      icon: 'plus' as const,
      keywords: 'create wizard',
      run: () => setDialog(<CreateWizard onClose={() => setDialog(null)} />),
    },
    {
      id: 'adopt-instance',
      label: 'Adopt instance…',
      group: 'Instance',
      icon: 'folder' as const,
      keywords: 'import existing',
      run: () => setDialog(<AdoptWizard onClose={() => setDialog(null)} />),
    },
    {
      id: 'refresh',
      label: 'Refresh statuses',
      group: 'Instance',
      icon: 'refresh' as const,
      hint: 'F5',
      run: () => void refresh(),
    },
    ...(currentId
      ? [
          {
            id: 'start-current',
            label: 'Start selected instance',
            group: 'Instance',
            icon: 'play' as const,
            keywords: 'boot run',
            run: () => void startCurrent(),
          },
          {
            id: 'stop-current',
            label: 'Stop selected instance',
            group: 'Instance',
            icon: 'stop' as const,
            keywords: 'halt',
            run: () => void stopCurrent(),
          },
        ]
      : []),
    {
      id: 'marketplace-index',
      label: 'Index GitHub module…',
      group: 'Marketplace',
      icon: 'folder' as const,
      keywords: 'github repo oca addon marketplace',
      run: () => setDialog(<IndexModuleDialog onClose={() => setDialog(null)} />),
    },
    {
      id: 'marketplace-featured',
      label: 'Refresh featured flags',
      group: 'Marketplace',
      icon: 'refresh' as const,
      keywords: 'apps odoo charts sync top',
      run: () => {
        void api.marketplace.sync_featured()
        setTab('marketplace')
      },
    },
    {
      id: 'preferences',
      label: 'Preferences…',
      group: 'System',
      icon: 'eye' as const,
      keywords: 'settings mode theme',
      run: () => setDialog(<PreferencesDialog onClose={() => setDialog(null)} />),
    },
    {
      id: 'event-log',
      label: 'Event log…',
      group: 'System',
      icon: 'copy' as const,
      keywords: 'audit debug',
      run: () => setDialog(<EventLogDialog onClose={() => setDialog(null)} />),
    },
    {
      id: 'about',
      label: 'About Odoo Vite',
      group: 'System',
      icon: 'info' as const,
      run: () => setDialog(<AboutDialog onClose={() => setDialog(null)} />),
    },
    {
      id: 'theme',
      label: 'Toggle light/dark theme',
      group: 'View',
      icon: 'eye' as const,
      keywords: 'dark light appearance mode',
      run: () => setTheme(resolveTheme(getTheme()) === 'dark' ? 'light' : 'dark'),
    },
  ]

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
          {/* keep-mounted: hidden views hold their scroll/selection/state;
              background polls stay live (docs/patterns.md) */}
          <div className="view" hidden={tab !== 'overview'}>
            <Overview />
          </div>
          <div className="view" hidden={tab !== 'databases'}>
            <Databases />
          </div>
          <div className="view" hidden={tab !== 'modules'}>
            <Modules />
          </div>
          <div className="view" hidden={tab !== 'marketplace'}>
            <Marketplace active={tab === 'marketplace'} />
          </div>
          <div className="view" hidden={tab !== 'configuration'}>
            <Configuration />
          </div>
          <div className="view" hidden={tab !== 'logs'}>
            <Logs active={tab === 'logs'} />
          </div>
          <div className="view" hidden={tab !== 'devtools'}>
            <DevTools />
          </div>
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

      {toasts.length > 0 && (
        <div className="snack-stack">
          {toasts.map((t) => (
            <div
              key={t.id}
              className={`snackbar toast-${t.level}`}
              role={t.level === 'error' ? 'alert' : 'status'}
              onClick={() => hideToast(t.id)}
            >
              <span className="toast-icon" aria-hidden="true">
                <Icon name={t.level === 'info' ? 'info' : 'alert'} size={14} />
              </span>
              <span className="toast-text">{t.text}</span>
              <button
                type="button"
                className="toast-x"
                aria-label="Dismiss"
                onClick={(e) => {
                  e.stopPropagation()
                  hideToast(t.id)
                }}
              >
                <Icon name="x" size={12} />
              </button>
            </div>
          ))}
        </div>
      )}

      {dialogs.map((d, i) => (
        <Fragment key={i}>{d}</Fragment>
      ))}

      {paletteOpen && (
        <CommandPalette commands={commands} onClose={() => setPaletteOpen(false)} />
      )}
    </div>
  )
}
