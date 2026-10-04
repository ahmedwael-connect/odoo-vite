/**
 * Configuration — odoo.conf table + common-key editors + raw key/value +
 * addons_path manager + registry metadata. Port of configuration.slint +
 * bridge _on_conf_action.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { copyWithToast } from '../clipboard'
import { getApi } from '../bridge'
import { Modal, useConfirm } from '../components/dialog'
import { Icon } from '../components/icons'
import {
  ActionButton,
  Card,
  DimText,
  ElidePath,
  EmptyState,
  ErrorText,
  LineList,
  Select,
  SpinInput,
  TextInput,
} from '../components/ui'
import { Banner } from '../components/widgets'
import { onEvent } from '../events'
import { useApp } from '../store'
import type { ConfView, Dict } from '../types'

interface Notice {
  text: string
  kind: 'info' | 'error'
}

const COMMON_KEYS = ['db_host', 'db_port', 'db_user', 'xmlrpc_port', 'logfile']
const CONF_LOG_LEVELS = ['info', 'debug', 'debug_sql', 'warning', 'error', 'critical']

interface AddonsEntry extends Dict {
  path: string
  enabled: boolean
  exists?: boolean
  modules?: number
  type?: string
}

/** 3.2.0 F2: mirrors core/path_types.PATH_TYPES (labels are UI-only). */
const PATH_TYPES = ['community', 'enterprise', 'custom', 'extra', 'unknown'] as const
const PATH_TYPE_LABEL: Record<string, string> = {
  community: 'Community',
  enterprise: 'Enterprise',
  custom: 'Custom',
  extra: 'Extra',
  unknown: 'Unknown',
}

/** Client mirror of core/addon_paths.derive_addons_path (enabled only, order kept). */
function derivePreview(entries: AddonsEntry[]): string {
  return entries
    .filter((e) => e.enabled && e.path)
    .map((e) => e.path)
    .join(',')
}

function AddonsDialog({
  instanceId,
  running,
  onClose,
}: {
  instanceId: string
  running: boolean
  onClose: () => void
}) {
  const api = getApi()
  const [entries, setEntries] = useState<AddonsEntry[]>([])
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [pending, setPending] = useState('')

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

  const append = (path: string) => {
    if (entries.some((e) => e.path === path)) {
      setError('Already in the list.')
      return
    }
    setError('')
    setNotice('')
    setEntries((prev) => [...prev, { path, enabled: true }])
  }

  const add = async () => {
    const res = await api.app.pick_dir('Add addons folder')
    if (!res.ok || !res.path) return
    if (entries.some((e) => e.path === res.path)) {
      setError('Already in the list.')
      setPending('')
      return
    }
    const looks = await api.config.looks_like_addons(res.path)
    if (!looks) {
      // Sprint 7 T7.4: warn, don't block — user can still add it.
      setPending(res.path)
      setError('')
      return
    }
    setPending('')
    append(res.path)
  }

  const addAnyway = () => {
    const path = pending
    setPending('')
    append(path)
  }

  const move = (i: number, dir: -1 | 1) => {
    setNotice('')
    setEntries((prev) => {
      const j = i + dir
      if (j < 0 || j >= prev.length) return prev
      const next = [...prev]
      ;[next[i], next[j]] = [next[j], next[i]]
      return next
    })
  }

  const remove = (i: number) => {
    setNotice('')
    setEntries((prev) => prev.filter((_, j) => j !== i))
  }

  const apply = async () => {
    setError('')
    const res = await api.config.apply_addons(instanceId, entries)
    if (res.ok) {
      setNotice(res.message) // backend already appends "(takes effect on next restart)"
      await load() // re-enrich exists/modules against the disk
    } else {
      setError(res.message)
    }
  }

  // 3.2.0 F2: classify every path server-side (registry-only write) and
  // re-read the enriched rows.
  const autoDetect = async () => {
    setError('')
    const res = await api.config.auto_type_addons(instanceId)
    if (res.ok) {
      setNotice(res.message)
      await load()
    } else {
      setError(res.message)
    }
  }

  const preview = derivePreview(entries)

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
      {running && (
        <Banner kind="warn">Instance is running — changes take effect on next restart.</Banner>
      )}
      <div className="addons-list">
        {entries.map((e, i) => (
          <div key={`${e.path}-${i}`} className="addons-row">
            <label className="checkbox" title="Include in addons_path">
              <input
                type="checkbox"
                checked={e.enabled}
                onChange={(ev) => {
                  setNotice('')
                  setEntries((prev) =>
                    prev.map((row, j) => (j === i ? { ...row, enabled: ev.target.checked } : row)),
                  )
                }}
              />
              <span />
            </label>
            <span className="mono elide" title={e.path}>
              {e.path}
            </span>
            {e.exists === false ? (
              <span className="gap-missing" title="Folder not found on disk">
                missing
              </span>
            ) : (
              <span className="dim-label" title="Module folders found">
                {e.modules ?? 0} modules
              </span>
            )}
            <Select
              value={(PATH_TYPES as readonly string[]).includes(e.type ?? '') ? e.type : 'unknown'}
              onChange={(ev) => {
                setNotice('')
                setEntries((prev) =>
                  prev.map((row, j) => (j === i ? { ...row, type: ev.target.value } : row)),
                )
              }}
              title="Path type"
              aria-label={`Type of ${e.path}`}
            >
              {PATH_TYPES.map((t) => (
                <option key={t} value={t}>
                  {PATH_TYPE_LABEL[t]}
                </option>
              ))}
            </Select>
            <button
              type="button"
              className="btn icon"
              disabled={i === 0}
              onClick={() => move(i, -1)}
              title="Move up"
              aria-label={`Move up ${e.path}`}
            >
              <Icon name="chevron-up" size={12} />
            </button>
            <button
              type="button"
              className="btn icon"
              disabled={i === entries.length - 1}
              onClick={() => move(i, 1)}
              title="Move down"
              aria-label={`Move down ${e.path}`}
            >
              <Icon name="chevron-down" size={12} />
            </button>
            <button
              type="button"
              className="btn icon"
              onClick={() => remove(i)}
              title="Remove from list"
              aria-label={`Remove ${e.path}`}
            >
              <Icon name="x" size={12} />
            </button>
          </div>
        ))}
        {entries.length === 0 && <EmptyState text="No addon paths loaded." />}
      </div>
      <DimText>
        <span className="mono">derived addons_path: </span>
        <span className="mono" title={preview}>
          {preview || '(empty)'}
        </span>
      </DimText>
      {pending && (
        <Banner kind="warn">
          {pending} does not look like an addons folder (no module subfolders) — add it anyway?
        </Banner>
      )}
      <div className="btn-row">
        <ActionButton onClick={() => void add()}>Add folder…</ActionButton>
        <ActionButton
          title="Classify paths as Community/Enterprise/Custom/Extra (registry only)"
          onClick={() => void autoDetect()}
        >
          Auto-detect types
        </ActionButton>
        {pending && (
          <ActionButton primary onClick={addAnyway}>
            Add anyway
          </ActionButton>
        )}
        {pending && (
          <ActionButton onClick={() => setPending('')}>
            Dismiss
          </ActionButton>
        )}
      </div>
      <ErrorText text={error} />
      {notice && <Banner kind="ok">{notice}</Banner>}
    </Modal>
  )
}

export default function Configuration() {
  const { current, currentId, statuses, setDialog, setBusy, busy } = useApp()
  const confirm = useConfirm()

  const [view, setView] = useState<ConfView>({})
  const [common, setCommon] = useState<Dict>({})
  const [rawKey, setRawKey] = useState('')
  const [rawValue, setRawValue] = useState('')
  const [notice, setNotice] = useState<Notice | null>(null)
  const [meta, setMeta] = useState({
    description: '',
    workers: 0,
    logLevel: 'info',
    python: '',
    auto: [] as string[],
    pending: [] as string[],
  })
  const [metaErr, setMetaErr] = useState('')
  // 3.2.0 F1: module-update queue editor (once/every target).
  const [updText, setUpdText] = useState('')
  const [updTarget, setUpdTarget] = useState<'once' | 'every'>('once')
  const [loadErr, setLoadErr] = useState('')
  // 3.1.0 B10: unsaved edits must survive the global refresh event.
  const [dirty, setDirty] = useState(false)
  const [refreshSkipped, setRefreshSkipped] = useState(false)
  // 3.1.0 N2: filter + copy over the raw odoo.conf lines.
  const [confQ, setConfQ] = useState('')
  const confLines = (view.lines ?? []).filter(
    (l) => !confQ.trim() || l.toLowerCase().includes(confQ.trim().toLowerCase()),
  )
  const dirtyRef = useRef(false)
  useEffect(() => {
    dirtyRef.current = dirty
  }, [dirty])
  // 3.3.0 P2: request guard — a slow config.read for the previous instance
  // resolving after a switch used to overwrite view/common with the old
  // instance's conf AND wipe unsaved edits (setDirty(false) in finally).
  const loadSeq = useRef(0)

  const load = useCallback(async (id: string) => {
    const seq = ++loadSeq.current
    const stale = () => seq !== loadSeq.current
    try {
      const v = (await getApi().config.read(id)) as ConfView
      if (stale()) return
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
        auto: v.auto_update_modules ?? [],
        pending: v.pending_update_modules ?? [],
      })
    } catch (err) {
      if (stale()) return
      setLoadErr(err instanceof Error ? err.message : String(err))
    } finally {
      if (!stale()) {
        setDirty(false)
        setRefreshSkipped(false)
      }
    }
  }, [])

  useEffect(() => {
    if (!currentId) return
    void load(currentId)
    const off = onEvent('refresh', () => {
      if (!currentId) return
      if (dirtyRef.current) {
        // 3.1.0 B10: skip the auto-reload — it would discard edits.
        setRefreshSkipped(true)
        return
      }
      void load(currentId)
    })
    return off
  }, [currentId, load])

  if (!current || !currentId) {
    return <p className="empty-state dim-label">Select an instance</p>
  }

  const api = getApi()
  const hasBackup = Boolean(view.backup_path)
  const running = statuses.find((s) => s.id === currentId)?.status === 'running'

  // Field edits mark the form dirty (B10) so the refresh event skips reload.
  const editCommon = (fn: (prev: Dict) => Dict) => {
    setDirty(true)
    setCommon(fn)
  }
  const editMeta = (fn: (prev: typeof meta) => typeof meta) => {
    setDirty(true)
    setMeta(fn)
  }

  const saveCommon = async () => {
    const changes: Dict = {}
    for (const key of COMMON_KEYS) {
      const next = common[key] ?? ''
      if (next !== (view.common?.[key] ?? '')) changes[key] = next
    }
    if (Object.keys(changes).length === 0) {
      setNotice({ text: 'No changes to save.', kind: 'info' })
      return
    }
    setBusy(true)
    try {
      const res = await api.config.save(currentId, changes)
      setNotice(res.ok ? null : { text: res.message, kind: 'error' })
      if (res.ok) await load(currentId)
    } finally {
      setBusy(false)
    }
  }

  const rawSet = async () => {
    if (busy) return
    const key = rawKey.trim()
    if (!key) {
      setNotice({ text: 'Enter a key name first.', kind: 'info' })
      return
    }
    setBusy(true)
    try {
      const res = await api.config.save(currentId, { [key]: rawValue === '' ? null : rawValue })
      setRawKey('')
      setRawValue('')
      setNotice(res.ok ? null : { text: res.message, kind: 'error' })
      if (res.ok) await load(currentId)
    } finally {
      setBusy(false)
    }
  }

  const restore = async () => {
    setBusy(true)
    try {
      const res = await api.config.restore(currentId)
      setNotice(res.ok ? null : { text: res.message, kind: 'error' })
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
      setNotice(res.ok ? null : { text: res.message, kind: 'error' })
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
      auto_update_modules: meta.auto,
      pending_update_modules: meta.pending,
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
      setNotice(res.ok ? null : { text: res.message, kind: 'error' })
      if (res.ok) await load(currentId)
    } finally {
      setBusy(false)
    }
  }

  const browsePython = async () => {
    const res = await api.app.pick_file('Python interpreter', 'open')
    if (res.ok && res.path) editMeta((m) => ({ ...m, python: res.path ?? '' }))
  }

  // 3.2.0 F1: queue module updates (once = next start, every = each start).
  const queueUpdates = () => {
    const names = updText
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)
    if (names.length === 0) return
    if (updTarget === 'once') {
      editMeta((m) => {
        const merged = [...m.pending]
        for (const n of names) if (!merged.includes(n)) merged.push(n)
        return { ...m, pending: merged }
      })
    } else {
      editMeta((m) => {
        const merged = [...m.auto]
        for (const n of names) if (!merged.includes(n)) merged.push(n)
        return { ...m, auto: merged }
      })
    }
    setUpdText('')
  }

  const dropPending = (name: string) =>
    editMeta((m) => ({ ...m, pending: m.pending.filter((x) => x !== name) }))
  const dropAuto = (name: string) =>
    editMeta((m) => ({ ...m, auto: m.auto.filter((x) => x !== name) }))

  return (
    <div className="view conf-view">
      {refreshSkipped && dirty && (
        <Banner
          kind="warn"
          action={
            <ActionButton onClick={() => void load(currentId)}>
              Reload file (discard edits)
            </ActionButton>
          }
        >
          A background refresh was skipped — you have unsaved configuration edits.
        </Banner>
      )}
      <Card title="Configuration (odoo.conf)">
        {loadErr ? (
          <ErrorText text={loadErr} />
        ) : (
          <>
            <DimText>
              <ElidePath path={view.conf_path ?? ''} />
            </DimText>
            <div className="btn-row">
              <TextInput
                placeholder="Filter lines…"
                value={confQ}
                onChange={(e) => setConfQ(e.target.value)}
                aria-label="Filter configuration lines"
              />
              <ActionButton
                disabled={!confLines.length}
                title="Copy the visible configuration lines"
                onClick={() =>
                  void copyWithToast(confLines.join('\n'), `${confLines.length} lines`)
                }
              >
                Copy
              </ActionButton>
            </div>
            {confQ.trim() && (
              <DimText>
                {confLines.length} of {(view.lines ?? []).length} lines match
              </DimText>
            )}
            <LineList lines={confLines} maxHeight={300} />
          </>
        )}
      </Card>

      <Card
        title="Edit common keys"
        actions={
          <>
            <ActionButton primary disabled={busy} onClick={() => void saveCommon()}>
              Save changes
            </ActionButton>
            <ActionButton disabled={busy || !hasBackup} onClick={() => void restore()}>
              Restore last backup
            </ActionButton>
          </>
        }
      >
        <div className="grid2">
          {COMMON_KEYS.map((key) => (
            <label key={key} className="field">
              <span className="field-label mono">{key}</span>
              <TextInput
                value={String(common[key] ?? '')}
                onChange={(e) => editCommon((prev) => ({ ...prev, [key]: e.target.value }))}
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
              setDialog(
                <AddonsDialog
                  instanceId={currentId}
                  running={running}
                  onClose={() => setDialog(null)}
                />,
              )
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
        {notice && <Banner kind={notice.kind}>{notice.text}</Banner>}
        {view.backup_path ? (
          <DimText>
            Backup: <ElidePath path={view.backup_path} />
          </DimText>
        ) : (
          <DimText>No conf backup on disk yet.</DimText>
        )}
      </Card>

      <Card
        title="Advanced"
        actions={
          <ActionButton
            title="Rebuilds options from registry — manual edits lost"
            disabled={busy}
            onClick={() => void regenerate()}
          >
            Regenerate from registry…
          </ActionButton>
        }
      />

      <Card
        title="Metadata"
        actions={
          <ActionButton primary disabled={busy} onClick={() => void saveMeta()}>
            Save metadata
          </ActionButton>
        }
      >
        <label className="field">
          <span className="field-label">Description (registry only, for organization)</span>
          <TextInput
            value={meta.description}
            onChange={(e) => editMeta((m) => ({ ...m, description: e.target.value }))}
          />
        </label>
        <div className="grid2">
          <label className="field">
            <span className="field-label">Workers</span>
            <SpinInput
              min={0}
              max={64}
              value={meta.workers}
              onChange={(e) => editMeta((m) => ({ ...m, workers: Number(e.target.value) }))}
            />
          </label>
          <label className="field">
            <span className="field-label">Log level</span>
            <Select
              value={meta.logLevel}
              onChange={(e) => editMeta((m) => ({ ...m, logLevel: e.target.value }))}
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
            onChange={(e) => editMeta((m) => ({ ...m, python: e.target.value }))}
          />
          <ActionButton onClick={() => void browsePython()}>Browse…</ActionButton>
        </div>
        <div className="addons-path-row">
          <span className="field-label">Queue module updates</span>
          <TextInput
            placeholder="module_a, module_b"
            value={updText}
            onChange={(e) => setUpdText(e.target.value)}
            aria-label="Modules to update"
          />
          <Select
            value={updTarget}
            onChange={(e) => setUpdTarget(e.target.value === 'every' ? 'every' : 'once')}
            aria-label="Update frequency"
          >
            <option value="once">Next start only</option>
            <option value="every">Every start</option>
          </Select>
          <ActionButton disabled={busy || !updText.trim()} onClick={queueUpdates}>
            Add
          </ActionButton>
        </div>
        <div className="upd-chips" aria-label="Queued one-shot updates">
          {meta.pending.map((name) => (
            <span key={`pending-${name}`} className="chip edit">
              {name}
              <button
                type="button"
                className="btn icon"
                onClick={() => dropPending(name)}
                title="Remove from next-start queue"
                aria-label={`Remove ${name} from next-start queue`}
              >
                <Icon name="x" size={12} />
              </button>
            </span>
          ))}
          {meta.pending.length > 0 && (
            <ActionButton onClick={() => editMeta((m) => ({ ...m, pending: [] }))}>
              Clear queued
            </ActionButton>
          )}
        </div>
        <div className="upd-chips" aria-label="Every-start updates">
          {meta.auto.map((name) => (
            <span key={`auto-${name}`} className="chip edit">
              {name}
              <button
                type="button"
                className="btn icon"
                onClick={() => dropAuto(name)}
                title="Remove from every-start list"
                aria-label={`Remove ${name} from every-start list`}
              >
                <Icon name="x" size={12} />
              </button>
            </span>
          ))}
        </div>
        {meta.pending.length === 0 && meta.auto.length === 0 && (
          <DimText>
            No module updates configured — nothing is added to odoo-bin's -u argument on start.
          </DimText>
        )}
        <ErrorText text={metaErr} />
      </Card>
    </div>
  )
}
