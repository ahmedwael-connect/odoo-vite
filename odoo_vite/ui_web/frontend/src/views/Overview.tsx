/**
 * Overview — status, Server/Database cards, lifecycle actions.
 * Port of ui_slint/overview.slint + bridge ov-* handlers.
 */

import { useEffect, useState } from 'react'
import { getApi } from '../bridge'
import { useConfirm, useTypedConfirm } from '../components/dialog'
import { ActionButton, Card, ElidePath, EmptyState, StatusPill } from '../components/ui'
import { Modal } from '../components/dialog'
import { useApp } from '../store'
import type { InstanceRow } from '../types'

function CloneDialog({
  source,
  defaultName,
  defaultPort,
  onConfirm,
  onClose,
}: {
  source: InstanceRow
  defaultName: string
  defaultPort: number
  onConfirm: (name: string, port: number) => void
  onClose: () => void
}) {
  const [name, setName] = useState(defaultName)
  const [port, setPort] = useState(String(defaultPort))
  const ok = name.trim().length > 0
  return (
    <Modal
      title={`Clone "${source.name}"`}
      onClose={onClose}
      width={440}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn primary"
            disabled={!ok}
            onClick={() => onConfirm(name.trim(), Number(port) || defaultPort)}
          >
            Clone
          </button>
        </>
      }
    >
      <label className="field">
        <span className="field-label">New name</span>
        <input className="input" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
      </label>
      <label className="field">
        <span className="field-label">Port</span>
        <input className="input spin" value={port} onChange={(e) => setPort(e.target.value)} />
      </label>
    </Modal>
  )
}

export default function Overview() {
  const { current, currentId, statuses, setDialog, refresh, setBusy, busy } = useApp()
  const confirm = useConfirm()
  const typedConfirm = useTypedConfirm()
  const [ent, setEnt] = useState<{ text: string; warn: boolean }>({ text: '', warn: false })

  const status = statuses.find((s) => s.id === currentId)?.status ?? ''
  const running = status === 'running'

  useEffect(() => {
    if (!currentId) return
    let alive = true
    void getApi()
      .app.enterprise(currentId)
      .then((res) => {
        if (!alive) return
        const state = String((res.data as { state?: string } | undefined)?.state ?? '')
        setEnt({ text: res.message, warn: state === 'invalid' })
      })
      .catch(() => undefined)
    return () => {
      alive = false
    }
  }, [currentId, statuses])

  if (!current) {
    return <p className="empty-state dim-label">Select an instance</p>
  }

  const api = getApi()

  const act = async <T extends { ok: boolean; message: string }>(fn: () => Promise<T>): Promise<T> => {
    setBusy(true)
    try {
      return await fn()
    } finally {
      setBusy(false)
      refresh()
    }
  }

  const onStart = async () => {
    if (busy) return
    const res = await act(() => api.lifecycle.start(current.id, null, false))
    if (res.ok) return
    const data = res.data as { needs_confirm?: boolean; preview?: { detail?: string } } | undefined
    if (data?.needs_confirm) {
      const ok = await confirm({
        heading: 'Create database?',
        body: `${data.preview?.detail ?? res.message}\n\nRuns odoo-bin -i base, then starts.`,
        confirmLabel: 'Create + Start',
      })
      if (ok) await act(() => api.lifecycle.start(current.id, null, true))
    }
  }

  const onRemove = async () => {
    if (busy) return
    const adopted = current.mode === 'adopted'
    const ok = adopted
      ? await confirm({
          heading: `Remove "${current.name}"?`,
          body: 'Unregisters the instance. Files and DB are NOT touched.',
          confirmLabel: 'Remove',
          destructive: true,
        })
      : await typedConfirm({
          heading: `Remove "${current.name}"?`,
          body: 'Unregisters the instance. Files and DB are NOT touched.\nRe-type the name to confirm.',
          expected: current.name,
          confirmLabel: 'Remove',
          destructive: true,
        })
    if (!ok) return
    await act(() => api.lifecycle.remove(current.id))
  }

  const onClone = async () => {
    if (running || busy) return
    const suggested = await api.app.suggest_port((current.port || 8069) + 1)
    const defaultName = `${current.name} (clone)`
    await new Promise<void>((resolve) => {
      setDialog(
        <CloneDialog
          source={current}
          defaultName={defaultName}
          defaultPort={suggested}
          onClose={() => {
            setDialog(null)
            resolve()
          }}
          onConfirm={(name, port) => {
            setDialog(null)
            resolve()
            void act(() => api.lifecycle.clone(current.id, name, port))
          }}
        />,
      )
    })
  }

  const onExport = async () => {
    if (busy) return
    const picked = await api.app.pick_file('Export instance as…', 'save', 'Odoo Vite bundles (*.tar.gz)')
    if (!picked.ok || !picked.path) return
    await act(() => api.transfer.export_bundle(current.id, picked.path!))
  }

  return (
    <div className="view">
      <div className="view-title-row">
        <h1 className="title">{current.name}</h1>
        {currentId && <StatusPill status={status} />}
      </div>

      <Card title="Server">
        <dl className="kv">
          <dt>Version</dt>
          <dd>{current.version || '—'}</dd>
          <dt>Port</dt>
          <dd>{current.port}</dd>
          <dt>Path</dt>
          <dd>
            <ElidePath path={current.path} />
          </dd>
        </dl>
      </Card>

      <Card title="Database">
        <dl className="kv">
          <dt>Primary DB</dt>
          <dd>{current.primary_db || '—'}</dd>
          <dt>DB user</dt>
          <dd>{current.db_user ?? 'odoo'}</dd>
        </dl>
      </Card>

      <EmptyState text={ent.text} warn={ent.warn} />

      <div className="btn-row">
        <ActionButton primary disabled={running || busy} onClick={() => void onStart()}>
          Start
        </ActionButton>
        <ActionButton disabled={!running || busy} onClick={() => void act(() => api.lifecycle.stop(current.id))}>
          Stop
        </ActionButton>
        <ActionButton disabled={!running || busy} onClick={() => void act(() => api.lifecycle.restart(current.id))}>
          Restart
        </ActionButton>
        <ActionButton disabled={busy} onClick={() => void onRemove()} title="Remove the instance (databases are kept)">
          Remove
        </ActionButton>
        <ActionButton disabled={running || busy} onClick={() => void onClone()}>
          Clone
        </ActionButton>
        <ActionButton disabled={busy} onClick={() => void onExport()}>Export…</ActionButton>
      </div>
    </div>
  )
}
