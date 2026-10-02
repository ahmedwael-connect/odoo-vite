/**
 * Configuration — odoo.conf table + common-key editors + raw key/value +
 * addons_path manager + registry metadata. Port of configuration.slint +
 * bridge _on_conf_action.
 */

import { useCallback, useEffect, useState } from 'react'
import { getApi } from '../bridge'
import { Modal, useConfirm } from '../components/dialog'
import {
  ActionButton,
  Card,
  DimText,
  ElidePath,
  EmptyState,
  ErrorText,
  LineList,
  SectionHeader,
  Select,
  SpinInput,
  TextInput,
} from '../components/ui'
import { onEvent } from '../events'
import { useApp } from '../store'
import type { ConfView, Dict } from '../types'

const COMMON_KEYS = ['db_host', 'db_port', 'db_user', 'xmlrpc_port', 'logfile']
const CONF_LOG_LEVELS = ['info', 'debug', 'debug_sql', 'warning', 'error', 'critical']

interface AddonsEntry extends Dict {
  path: string
  enabled: boolean
  builtin?: boolean
}

function AddonsDialog({
  instanceId,
  onClose,
}: {
  instanceId: string
  onClose: () => void
}) {
  const api = getApi()
  const [entries, setEntries] = useState<AddonsEntry[]>([])
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      setEntries((await api.config.addons_state(instanceId)) as unknown as AddonsEntry[])
      setError('')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }, [api, instanceId])

  useEffect(() => {
    void load()
  }, [load])

  const add = async () => {
    const res = await api.app.pick_dir('Add addons folder')
    if (!res.ok || !res.path) return
    const looks = await api.config.looks_like_addons(res.path)
    if (!looks) {
      setError(`${res.path} does not look like an addons folder (no module subfolders).`)
      return
    }
    if (entries.some((e) => e.path === res.path)) {
      setError('Already in the list.')
      return
    }
    setError('')
    setEntries([...entries, { path: res.path, enabled: true }])
  }

  const apply = async () => {
    const res = await api.config.apply_addons(instanceId, entries)
    if (res.ok) onClose()
    else setError(res.message)
  }

  return (
    <Modal
      title="Manage addons_path"
      onClose={onClose}
      width={620}
      footer={
        <>
          <ActionButton onClick={onClose}>Cancel</ActionButton>
          <ActionButton primary onClick={() => void apply()}>
            Apply
          </ActionButton>
        </>
      }
    >
      <div className="addons-list">
        {entries.map((e, i) => (
          <div key={`${e.path}-${i}`} className="addons-row">
            <label className="checkbox" title={e.builtin ? 'Builtin (read-only)' : 'Include in addons_path'}>
              <input
                type="checkbox"
                checked={e.enabled}
                disabled={e.builtin}
                onChange={(ev) =>
                  setEntries((prev) =>
                    prev.map((row, j) => (j === i ? { ...row, enabled: ev.target.checked } : row)),
                  )
                }
              />
              <span />
            </label>
            <span className="mono elide" title={e.path}>
              {e.path}
            </span>
            {!e.builtin && (
              <ActionButton
                onClick={() => setEntries((prev) => prev.filter((_, j) => j !== i))}
                title="Remove from list"
              >
                ×
              </ActionButton>
            )}
          </div>
        ))}
        {entries.length === 0 && <EmptyState text="No addon paths loaded." />}
      </div>
      <div className="btn-row">
        <ActionButton onClick={() => void add()}>Add folder…</ActionButton>
      </div>
      <ErrorText text={error} />
    </Modal>
  )
}

export default function Configuration() {
  const { current, currentId, setDialog, setBusy, busy } = useApp()
  const confirm = useConfirm()

  const [view, setView] = useState<ConfView>({})
  const [common, setCommon] = useState<Dict>({})
  const [rawKey, setRawKey] = useState('')
  const [rawValue, setRawValue] = useState('')
  const [notice, setNotice] = useState('')
  const [meta, setMeta] = useState({ description: '', workers: 0, logLevel: 'info', python: '' })
  const [metaErr, setMetaErr] = useState('')
  const [loadErr, setLoadErr] = useState('')

  const load = useCallback(
    async (id: string) => {
      try {
        const v = (await getApi().config.read(id)) as ConfView
        if (v.error) {
          setLoadErr(v.error)
          setView({})
          setCommon({})
          return
        }
        setLoadErr('')
        setView(v)
        setCommon({ ...(v.common ?? {}) })
        setMeta({
          description: v.description ?? '',
          workers: v.workers ?? 0,
          logLevel: v.log_level ?? 'info',
          python: v.python_binary ?? '',
        })
      } catch (err) {
        setLoadErr(err instanceof Error ? err.message : String(err))
      }
    },
    [],
  )

  useEffect(() => {
    if (!currentId) return
    void load(currentId)
    const off = onEvent('refresh', () => {
      if (currentId) void load(currentId)
    })
    return off
  }, [currentId, load])

  if (!current || !currentId) {
    return <p className="empty-state dim-label">Select an instance</p>
  }

  const api = getApi()
  const hasBackup = Boolean(view.backup_path)

  const saveCommon = async () => {
    const changes: Dict = {}
    for (const key of COMMON_KEYS) {
      const next = common[key] ?? ''
      if (next !== (view.common?.[key] ?? '')) changes[key] = next
    }
    if (Object.keys(changes).length === 0) {
      setNotice('No changes to save.')
      return
    }
    setBusy(true)
    try {
      const res = await api.config.save(currentId, changes)
      setNotice(res.ok ? '' : res.message)
      if (res.ok) await load(currentId)
    } finally {
      setBusy(false)
    }
  }

  const rawSet = async () => {
    if (busy) return
    const key = rawKey.trim()
    if (!key) {
      setNotice('Enter a key name first.')
      return
    }
    setBusy(true)
    try {
      const res = await api.config.save(currentId, { [key]: rawValue === '' ? null : rawValue })
      setRawKey('')
      setRawValue('')
      setNotice(res.ok ? '' : res.message)
      if (res.ok) await load(currentId)
    } finally {
      setBusy(false)
    }
  }

  const restore = async () => {
    setBusy(true)
    try {
      const res = await api.config.restore(currentId)
      setNotice(res.ok ? '' : res.message)
      if (res.ok) await load(currentId)
    } finally {
      setBusy(false)
    }
  }

  const regenerate = async () => {
    const ok = await confirm({
      heading: 'Regenerate odoo.conf from registry?',
      body: 'Rebuilds [options] from registry fields — manual edits to the file will be overwritten.',
      confirmLabel: 'Regenerate',
      destructive: true,
    })
    if (!ok) return
    setBusy(true)
    try {
      const res = await api.config.regenerate(currentId)
      setNotice(res.ok ? '' : res.message)
      if (res.ok) await load(currentId)
    } finally {
      setBusy(false)
    }
  }

  const saveMeta = async () => {
    const payload = {
      description: meta.description,
      workers: Number(meta.workers) || 0,
      log_level: meta.logLevel,
      python_binary: meta.python.trim(),
    }
    const err = await api.config.validate_meta(payload)
    if (err) {
      setMetaErr(err)
      return
    }
    setMetaErr('')
    setBusy(true)
    try {
      const res = await api.config.meta_save(currentId, payload)
      setNotice(res.ok ? '' : res.message)
      if (res.ok) await load(currentId)
    } finally {
      setBusy(false)
    }
  }

  const browsePython = async () => {
    const res = await api.app.pick_file('Python interpreter', 'open')
    if (res.ok && res.path) setMeta((m) => ({ ...m, python: res.path ?? '' }))
  }

  return (
    <div className="view conf-view">
      <Card title="Configuration (odoo.conf)">
        {loadErr ? (
          <ErrorText text={loadErr} />
        ) : (
          <>
            <DimText>
              <ElidePath path={view.conf_path ?? ''} />
            </DimText>
            <LineList lines={view.lines ?? []} maxHeight={300} />
          </>
        )}
      </Card>

      <Card title="Edit common keys">
        <div className="grid2">
          {COMMON_KEYS.map((key) => (
            <label key={key} className="field">
              <span className="field-label mono">{key}</span>
              <TextInput
                value={String(common[key] ?? '')}
                onChange={(e) => setCommon((prev) => ({ ...prev, [key]: e.target.value }))}
              />
            </label>
          ))}
        </div>
        <div className="addons-path-row">
          <span className="field-label mono">addons_path</span>
          <span className="dim-label elide" title={String(view.addons_path ?? '')}>
            {String(view.addons_path ?? '—')}
          </span>
          <ActionButton
            onClick={() =>
              setDialog(<AddonsDialog instanceId={currentId} onClose={() => setDialog(null)} />)
            }
          >
            Manage…
          </ActionButton>
        </div>
        <div className="btn-row">
          <TextInput
            placeholder="raw key"
            value={rawKey}
            onChange={(e) => setRawKey(e.target.value)}
          />
          <TextInput
            placeholder="value (empty deletes the key)"
            value={rawValue}
            onChange={(e) => setRawValue(e.target.value)}
          />
          <ActionButton disabled={busy} onClick={() => void rawSet()}>
            Set
          </ActionButton>
        </div>
        {notice && <EmptyState text={notice} warn />}
        <div className="btn-row">
          <ActionButton primary disabled={busy} onClick={() => void saveCommon()}>
            Save changes
          </ActionButton>
          <ActionButton disabled={busy || !hasBackup} onClick={() => void restore()}>
            Restore last backup
          </ActionButton>
        </div>
        {view.backup_path ? (
          <DimText>
            Backup: <ElidePath path={view.backup_path} />
          </DimText>
        ) : (
          <DimText>No conf backup on disk yet.</DimText>
        )}
      </Card>

      <Card title="Advanced">
        <ActionButton
          title="Rebuilds options from registry — manual edits lost"
          disabled={busy}
          onClick={() => void regenerate()}
        >
          Regenerate from registry…
        </ActionButton>
      </Card>

      <Card title="Metadata">
        <label className="field">
          <span className="field-label">Description (registry only, for organization)</span>
          <TextInput
            value={meta.description}
            onChange={(e) => setMeta((m) => ({ ...m, description: e.target.value }))}
          />
        </label>
        <div className="grid2">
          <label className="field">
            <span className="field-label">Workers</span>
            <SpinInput
              min={0}
              max={64}
              value={meta.workers}
              onChange={(e) => setMeta((m) => ({ ...m, workers: Number(e.target.value) }))}
            />
          </label>
          <label className="field">
            <span className="field-label">Log level</span>
            <Select
              value={meta.logLevel}
              onChange={(e) => setMeta((m) => ({ ...m, logLevel: e.target.value }))}
            >
              {CONF_LOG_LEVELS.map((lvl) => (
                <option key={lvl} value={lvl}>
                  {lvl}
                </option>
              ))}
            </Select>
          </label>
        </div>
        <DimText>
          Workers: 0 = single-process dev mode. Above 0 needs a free gevent/longpolling port and
          more RAM; cron moves to a dedicated worker. When in doubt, keep 0.
        </DimText>
        <div className="addons-path-row">
          <span className="field-label">Custom interpreter</span>
          <TextInput
            placeholder="empty = venv's own python"
            value={meta.python}
            onChange={(e) => setMeta((m) => ({ ...m, python: e.target.value }))}
          />
          <ActionButton onClick={() => void browsePython()}>Browse…</ActionButton>
        </div>
        <ErrorText text={metaErr} />
        <SectionHeader text="" />
        <ActionButton primary disabled={busy} onClick={() => void saveMeta()}>
          Save metadata
        </ActionButton>
      </Card>
    </div>
  )
}
