/**
 * DevTools — RPC connection, model inspector, paged record browser,
 * cron jobs, launch/editors, Odoo shell, module tests.
 * Port of devtools.slint + bridge _on_dev_action/_paint_* flows.
 */

import { useCallback, useEffect, useState } from 'react'
import { copyWithToast } from '../clipboard'
import { getApi } from '../bridge'
import { Modal, useConfirm, useTypedConfirm, useProgressRun } from '../components/dialog'
import { Icon } from '../components/icons'
import { SelectionList, type SelRow } from '../components/selection'
import {
  ActionButton,
  Card,
  DimText,
  EmptyState,
  LineList,
  PasswordInput,
  SectionHeader,
  Select,
  TextInput,
} from '../components/ui'
import { onEvent, route } from '../events'
import { useApp } from '../store'
import type { Dict, ModelEntry } from '../types'

const OPERATORS = ['=', '!=', 'like', 'ilike', '>', '<', '>=', '<=']
const SHELL_CAP = 1000

function RecordDialog({
  title,
  fields,
  initial,
  onSave,
  onClose,
}: {
  title: string
  fields: string[]
  initial: Record<string, string>
  onSave: (values: Record<string, string>) => void
  onClose: () => void
}) {
  const [values, setValues] = useState<Record<string, string>>(() => {
    const seed: Record<string, string> = {}
    for (const f of fields) seed[f] = initial[f] ?? ''
    return seed
  })
  return (
    <Modal
      title={title}
      onClose={onClose}
      width={520}
      footer={
        <>
          <ActionButton onClick={onClose}>Cancel</ActionButton>
          <ActionButton primary onClick={() => onSave(values)}>
            Save
          </ActionButton>
        </>
      }
    >
      {fields.map((f) => (
        <label key={f} className="field">
          <span className="field-label mono">{f}</span>
          <TextInput value={values[f] ?? ''} onChange={(e) => setValues((v) => ({ ...v, [f]: e.target.value }))} />
        </label>
      ))}
      {fields.length === 0 && <EmptyState text="No editable fields." />}
    </Modal>
  )
}

const yn = (v: unknown) => (v ? 'yes' : '—')
const flags = (f: Dict) =>
  [f.required && 'req', f.readonly && 'ro', f.store && 'store', f.compute && 'computed']
    .filter(Boolean)
    .join(' ')
const groupName = (g: unknown) =>
  Array.isArray(g) ? String(g[1] ?? g[0]) : g ? String(g) : 'everyone'

/** Full model metadata: fields + constraints + access (v2 §44). */
function MetaDetails({ meta }: { meta: Dict }) {
  const fields = (meta.fields as Dict[] | undefined) ?? []
  const constraints = (meta.constraints as Dict[] | undefined) ?? []
  const access = (meta.access as Dict[] | undefined) ?? []
  if (!fields.length && !constraints.length && !access.length) return null
  return (
    <div className="meta-details">
      {fields.length > 0 && (
        <>
          <SectionHeader text={`Fields (${fields.length})`} />
          <div className="meta-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Label</th>
                  <th>Type</th>
                  <th>Relation</th>
                  <th>Flags</th>
                </tr>
              </thead>
              <tbody>
                {fields.map((f) => (
                  <tr key={String(f.name ?? '')}>
                    <td className="mono">{String(f.name ?? '')}</td>
                    <td>{String(f.field_description ?? '')}</td>
                    <td>{String(f.ttype ?? '')}</td>
                    <td className="mono">{String(f.relation ?? '')}</td>
                    <td>{flags(f)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {constraints.length > 0 && (
        <>
          <SectionHeader text={`Constraints (${constraints.length})`} />
          <div className="meta-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Type</th>
                  <th>Definition</th>
                </tr>
              </thead>
              <tbody>
                {constraints.map((c, i) => (
                  <tr key={`${String(c.name ?? i)}`}>
                    <td className="mono">{String(c.name ?? '')}</td>
                    <td>{String(c.type ?? '')}</td>
                    <td>{String(c.definition ?? '')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {access.length > 0 && (
        <>
          <SectionHeader text={`Access rules (${access.length})`} />
          <div className="meta-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Group</th>
                  <th>Read</th>
                  <th>Write</th>
                  <th>Create</th>
                  <th>Delete</th>
                </tr>
              </thead>
              <tbody>
                {access.map((a, i) => (
                  <tr key={`${String(a.name ?? i)}`}>
                    <td className="mono">{String(a.name ?? '')}</td>
                    <td>{groupName(a.group_id)}</td>
                    <td>{yn(a.perm_read)}</td>
                    <td>{yn(a.perm_write)}</td>
                    <td>{yn(a.perm_create)}</td>
                    <td>{yn(a.perm_unlink)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}

export default function DevTools() {
  const { current, currentId, setDialog, setBusy, busy } = useApp()
  const confirm = useConfirm()
  const typedConfirm = useTypedConfirm()
  const runProgress = useProgressRun()

  const [rpcUser, setRpcUser] = useState('')
  const [rpcPass, setRpcPass] = useState('')
  const [rpcRemember, setRpcRemember] = useState(false)
  const [rpcStatus, setRpcStatus] = useState('Not connected.')

  const [models, setModels] = useState<ModelEntry[]>([])
  const [modelSelected, setModelSelected] = useState('')
  const [meta, setMeta] = useState<Dict>({})
  const [metaLine, setMetaLine] = useState('')
  const [modelsEmpty, setModelsEmpty] = useState('Connect to an instance to inspect models.')

  const [domField, setDomField] = useState('')
  const [domOp, setDomOp] = useState(0)
  const [domValue, setDomValue] = useState('')
  const [records, setRecords] = useState<Dict[]>([])
  const [recSelected, setRecSelected] = useState('')
  const [recCounts, setRecCounts] = useState('')
  const [recEmpty, setRecEmpty] = useState('No query yet — search above.')
  const [recPage, setRecPage] = useState('No query yet.')
  const [recRows, setRecRows] = useState<SelRow[]>([])

  const [crons, setCrons] = useState<string[]>([])
  const [cronEmpty, setCronEmpty] = useState('No cron jobs loaded — press Refresh.')
  const [cronQ, setCronQ] = useState('')
  // 3.1.0 N2: PATH probe result for VS Code / Cursor (null until first read).
  const [editors, setEditors] = useState<Dict | null>(null)

  const [shellRunning, setShellRunning] = useState(false)
  const [shellStatus, setShellStatus] = useState('Shell not running.')
  const [shellLines, setShellLines] = useState<string[]>([])
  const [shellIn, setShellIn] = useState('')
  const [watchOn, setWatchOn] = useState(false)
  const [watchMsg, setWatchMsg] = useState('Idle — not watching.')

  const [testModule, setTestModule] = useState('')
  const [testDb, setTestDb] = useState('')

  const [devBusy, setDevBusy] = useState(false)

  const api = getApi()
  const hasInstance = Boolean(current && currentId)

  const runDev = useCallback(
    async <T,>(fn: () => Promise<T>): Promise<T | undefined> => {
      setDevBusy(true)
      setBusy(true)
      try {
        return await fn()
      } catch (err) {
        route({
          kind: 'message',
          payload: { text: err instanceof Error ? err.message : String(err), level: 'error' },
        })
        return undefined
      } finally {
        setDevBusy(false)
        setBusy(false)
      }
    },
    [setBusy],
  )

  // reset per instance (Slint _reset_dev_view parity)
  useEffect(() => {
    setModels([])
    setModelSelected('')
    setMeta({})
    setMetaLine('')
    setModelsEmpty('Connect to an instance to inspect models.')
    setRecords([])
    setRecSelected('')
    setRecCounts('')
    setRecEmpty('No query yet — search above.')
    setRecPage('No query yet.')
    setCrons([])
    setCronEmpty('No cron jobs loaded — press Refresh.')
    setRpcStatus('Not connected.')
    setShellRunning(false)
    setShellStatus('Shell not running.')
    setShellLines([])
  }, [currentId])

  // ------------------------------------------------------------- events
  useEffect(() => {
    if (!currentId) return
    const mine = (p: Dict) => !p.instance_id || p.instance_id === currentId

    const offRpc = onEvent('dev-rpc', (p) => {
      if (mine(p)) setRpcStatus(String(p.message ?? ''))
    })
    const offModels = onEvent('dev-models', (p) => {
      if (!mine(p)) return
      const list = (p.models ?? []) as ModelEntry[]
      setModels(list.filter((m) => m.technical))
      setModelsEmpty(list.length ? '' : 'No models found.')
    })
    const offMeta = onEvent('dev-meta', (p) => {
      if (!mine(p)) return
      const m = (p.meta ?? {}) as Dict
      setMeta(m)
      void api.devtools.format_meta_line(m).then(setMetaLine)
    })
    const offRecords = onEvent('dev-records', (p) => {
      if (!mine(p)) return
      const recs = (p.records ?? []) as Dict[]
      const offset = Number(p.offset ?? 0)
      const hasMore = Boolean(p.more)
      setRecords(recs)
      setRecEmpty(recs.length ? '' : 'No records found.')
      const page = Math.floor(offset / 50) + 1
      setRecPage(`Page ${page} (offset ${offset})${hasMore ? ' — more' : ''}`)
      setRecCounts(`${recs.length} record(s)`)
      setRecSelected((prev) => (prev && recs.some((r) => String(r.id) === prev) ? prev : ''))
      void Promise.all(
        recs.map(async (r) => {
          const label = await api.devtools.format_record_label(r)
          return { id: String(r.id ?? ''), label }
        }),
      ).then((pairs) => {
        const byId = new Map(pairs.map((x) => [x.id, x.label]))
        setRecRows(pairs.map((x) => ({ id: x.id, title: byId.get(x.id) ?? x.id })))
      })
    })
    const offCrons = onEvent('dev-crons', (p) => {
      if (!mine(p)) return
      const list = (p.crons ?? []) as Dict[]
      setCronEmpty(list.length ? '' : 'No cron jobs found.')
      void Promise.all(list.map((c) => api.devtools.format_cron_line(c))).then(setCrons)
    })
    const offWatch = onEvent('dev-watch', (p) => {
      if (!mine(p)) return
      const state = String(p.state ?? '')
      setWatchMsg(String(p.message ?? state))
      if (state === 'started') setWatchOn(true)
      else if (state === 'stopped') setWatchOn(false)
    })
    return () => {
      offRpc()
      offModels()
      offMeta()
      offRecords()
      offCrons()
      offWatch()
    }
  }, [currentId, api])

  // ------------------------------------------------- watch state per instance
  useEffect(() => {
    if (!currentId) {
      setWatchOn(false)
      setWatchMsg('Idle — not watching.')
      return
    }
    void api.watch.status(currentId).then((s) => {
      const watching = Boolean(s && s.watching)
      setWatchOn(watching)
      if (!watching) setWatchMsg('Idle — not watching.')
    })
  }, [currentId, api])

  // 3.1.0 N2: one PATH probe per mount — gates the editor buttons below.
  useEffect(() => {
    let stopped = false
    void api.devtools
      .detect_editors()
      .then((found) => {
        if (!stopped && found) setEditors(found as Dict)
      })
      .catch(() => undefined)
    return () => {
      stopped = true
    }
  }, [api])

  // ------------------------------------------------------- shell polling
  useEffect(() => {
    if (!shellRunning) return
    let stopped = false
    const tick = async () => {
      if (stopped) return
      try {
        const res = await api.devtools.shell_poll()
        if (stopped) return
        if (res.lines.length) {
          setShellLines((prev) => {
            const next = [...prev, ...res.lines.map((l) => l.slice(0, 2000))]
            return next.length > SHELL_CAP ? next.slice(next.length - SHELL_CAP) : next
          })
        }
        if (!res.running) {
          setShellRunning(false)
          setShellStatus(
            res.exit_code === null || res.exit_code === undefined
              ? 'Shell stopped.'
              : `Shell exited (code ${res.exit_code}).`,
          )
        }
      } catch {
        /* transient */
      }
    }
    const timer = window.setInterval(() => void tick(), 500)
    void tick()
    return () => {
      stopped = true
      window.clearInterval(timer)
    }
  }, [shellRunning, api])

  if (!hasInstance) {
    return <p className="empty-state dim-label">Select an instance</p>
  }

  const iid = currentId!
  const err = (text: string) => route({ kind: 'message', payload: { text, level: 'error' } })

  // ---------------------------------------------------------------- rpc
  const rpcConnect = () =>
    void runDev(async () => {
      const res = await api.devtools.rpc_connect(iid, rpcUser.trim(), rpcPass, rpcRemember)
      if (res.ok) {
        setRpcPass('')
        const modelsRes = await api.devtools.list_models(iid)
        if (!modelsRes.ok) err(modelsRes.message)
      }
    })

  const reloadModels = () =>
    void runDev(async () => {
      const res = await api.devtools.list_models(iid)
      if (!res.ok) err(res.message)
    })

  const pickModel = (id: string) => {
    setModelSelected(id)
    void runDev(async () => {
      const res = await api.devtools.model_metadata(iid, id)
      if (!res.ok) err(res.message)
    })
  }

  // ------------------------------------------------------------- records
  const watchToggle = () =>
    void runDev(async () => {
      const res = watchOn ? await api.watch.stop(iid) : await api.watch.start(iid)
      if (!res.ok) err(res.message)
      else {
        setWatchOn(!watchOn)
        setWatchMsg(res.message)
      }
    })

  const recSearch = () =>
    void runDev(async () => {
      const res = await api.devtools.rec_search(iid, domField.trim(), OPERATORS[domOp], domValue)
      if (!res.ok) err(res.message)
    })

  const recPageMove = (delta: number) =>
    void runDev(async () => {
      const res = await api.devtools.rec_page(iid, delta)
      if (!res.ok) err(res.message)
    })

  const selectedRecord = (): Dict | null =>
    records.find((r) => String(r.id) === recSelected) ?? null

  const recNew = () => {
    if (!modelSelected) return err('Pick a model first')
    void runDev(async () => {
      const fields = ((await api.devtools.editable_fields(meta, null)) as string[]) ?? []
      if (fields.length === 0) {
        err('No editable fields loaded — pick a model first')
        return
      }
      setDialog(
        <RecordDialog
          title={`New ${modelSelected}`}
          fields={fields}
          initial={{}}
          onClose={() => setDialog(null)}
          onSave={(values) => {
            setDialog(null)
            void runDev(async () => {
              const res = await api.devtools.rec_create(iid, values)
              if (!res.ok) err(res.message)
            })
          }}
        />,
      )
    })
  }

  const recEdit = () => {
    const record = selectedRecord()
    if (!record) return err('Select a record first')
    const id = intId(record)
    if (id === null) return err('Select a record first')
    void runDev(async () => {
      const fields = ((await api.devtools.editable_fields(meta, record)) as string[]) ?? []
      if (fields.length === 0) {
        err('No editable fields loaded — pick a model first')
        return
      }
      const initial: Record<string, string> = {}
      for (const f of fields) initial[f] = String(record[f] ?? '')
      setDialog(
        <RecordDialog
          title={`Edit ${modelSelected} #${id}`}
          fields={fields}
          initial={initial}
          onClose={() => setDialog(null)}
          onSave={(values) => {
            setDialog(null)
            void runDev(async () => {
              const diff = (await api.devtools.diff_record(record, values)) as Dict
              const keys = Object.keys(diff)
              if (keys.length === 0) {
                err('No changes vs current values')
                return
              }
              const preview = keys
                .map((k) => {
                  const pair = diff[k] as [unknown, unknown]
                  return `${k}: ${JSON.stringify(pair[0])} → ${JSON.stringify(pair[1])}`
                })
                .join('\n')
              const changed: Dict = {}
              for (const k of keys) changed[k] = (diff[k] as [unknown, unknown])[1]
              const ok = await confirm({
                heading: `Update ${modelSelected} #${id}?`,
                body: `Changed fields:\n${preview}`,
                confirmLabel: 'Apply update',
              })
              if (!ok) return
              const res = await api.devtools.rec_update(iid, id, changed)
              if (!res.ok) err(res.message)
            })
          }}
        />,
      )
    })
  }

  const recDelete = () => {
    const record = selectedRecord()
    if (!record) return err('Select a record first')
    if (modelSelected.startsWith('ir.')) {
      return err(
        `Refusing to delete from system model '${modelSelected}' — framework metadata rows are off-limits, no exceptions`,
      )
    }
    const id = intId(record)
    if (id === null) return err('Select a record first')
    const shown = String(record.display_name ?? record.name ?? record.id)
    const expected = String(record.display_name ?? record.name ?? record.id)
    void (async () => {
      const ok = await typedConfirm({
        heading: `Delete ${modelSelected} '${shown}'?`,
        body:
          'This permanently deletes the record. Odoo itself may refuse when dependents block it — ' +
          `whatever it reports is shown verbatim. There is no undo.\n\nType ${JSON.stringify(expected)} to confirm.`,
        expected,
        confirmLabel: 'Delete permanently',
      })
      if (!ok) return
      void runDev(async () => {
        const res = await api.devtools.rec_delete(iid, id, expected)
        if (!res.ok) err(res.message)
      })
    })()
  }

  // --------------------------------------------------------------- crons
  const cronRefresh = () =>
    void runDev(async () => {
      const res = await api.devtools.cron_refresh(iid)
      if (!res.ok) err(res.message)
    })

  // ------------------------------------------------------------- shell
  const shellStart = () => {
    setShellLines([])
    setShellStatus('Starting shell…')
    setShellRunning(true)
    void runDev(async () => {
      const res = await api.devtools.shell_start(iid)
      if (!res.ok) {
        setShellRunning(false)
        setShellStatus(res.message)
        err(res.message)
      } else {
        setShellStatus(res.message || 'Shell running.')
      }
    })
  }

  const shellStop = () =>
    void runDev(async () => {
      const res = await api.devtools.shell_stop()
      if (!res.ok) err(res.message)
    })

  const shellSend = () => {
    const text = shellIn.trim()
    if (!text || busy) return
    setShellIn('')
    void runDev(async () => {
      const res = await api.devtools.shell_send(text)
      if (!res.ok) err(res.message)
    })
  }

  // -------------------------------------------------------------- tests
  const testRun = () => {
    const module = testModule.trim()
    if (!module) return err('Enter the module technical name to test')
    const primary = (current?.primary_db ?? 'odoo').trim()
    const target = testDb.trim() || `${primary}_test`
    if (target === primary) {
      return err('Refusing to run tests on the PRIMARY database — pick a disposable test database')
    }
    void (async () => {
      const ok = await confirm({
        heading: `Run ${module} tests on '${target}'?`,
        body: 'Tests create/modify/destroy data — never the primary DB.',
        confirmLabel: 'Run tests',
      })
      if (!ok) return
      setDevBusy(true)
      setBusy(true)
      try {
        const res = await runProgress(`Tests: ${module} on ${target}`, (opId) =>
          api.modules.run_tests(iid, target, module, opId),
        )
        if (!res.ok) err(res.message)
      } finally {
        setDevBusy(false)
        setBusy(false)
      }
    })()
  }

  const modelRows: SelRow[] = models.map((m) => ({
    id: m.technical,
    title: m.technical,
    badge: m.display,
  }))
  const recSelectedRecord = selectedRecord()

  return (
    <div className="view devtools-view">
      <Card
        title="RPC connection"
        actions={
          <ActionButton primary disabled={devBusy} onClick={rpcConnect}>
            Connect
          </ActionButton>
        }
      >
        <div className="btn-row">
          <TextInput
            placeholder="Odoo user (default admin)"
            value={rpcUser}
            onChange={(e) => setRpcUser(e.target.value)}
          />
          <PasswordInput placeholder="password" value={rpcPass} onChange={(e) => setRpcPass(e.target.value)} />
          <label className="checkbox inline">
            <input
              type="checkbox"
              checked={rpcRemember}
              onChange={(e) => setRpcRemember(e.target.checked)}
            />
            <span />
            Remember
          </label>
        </div>
        <DimText>{rpcStatus}</DimText>
      </Card>

      <Card
        title="Model Inspector"
        actions={
          <ActionButton disabled={devBusy} onClick={reloadModels}>
            Reload models
          </ActionButton>
        }
      >
        <SelectionList
          rows={modelRows}
          selectedId={modelSelected}
          onPick={pickModel}
          showFilter
          maxHeight={220}
          counts={`${models.length} model(s)`}
          emptyState={<EmptyState text={modelsEmpty} />}
        />
        <DimText>{metaLine}</DimText>
        <MetaDetails meta={meta} />
      </Card>

      <Card
        title="Record Browser"
        actions={
          <>
            <ActionButton disabled={devBusy} onClick={recNew}>
              New…
            </ActionButton>
            <ActionButton disabled={devBusy || !recSelected} onClick={recEdit}>
              Edit…
            </ActionButton>
          </>
        }
        menu={[
          {
            label: 'Delete record…',
            danger: true,
            disabled: devBusy || !recSelected,
            onClick: recDelete,
          },
        ]}
      >
        <div className="btn-row">
          <TextInput
            placeholder="field (empty = all)"
            value={domField}
            onChange={(e) => setDomField(e.target.value)}
          />
          <Select value={String(domOp)} onChange={(e) => setDomOp(Number(e.target.value))}>
            {OPERATORS.map((op, i) => (
              <option key={op} value={i}>
                {op}
              </option>
            ))}
          </Select>
          <TextInput
            placeholder="value"
            value={domValue}
            onChange={(e) => setDomValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') recSearch()
            }}
          />
          <ActionButton disabled={devBusy} onClick={recSearch}>
            Search
          </ActionButton>
        </div>
        <SelectionList
          rows={recRows}
          selectedId={recSelected}
          onPick={setRecSelected}
          maxHeight={220}
          counts={recCounts}
          emptyState={<EmptyState text={recEmpty} />}
        />
        <div className="split-row">
          <ActionButton disabled={devBusy} onClick={() => recPageMove(-1)}>
            <Icon name="chevron-left" size={12} />
            Prev
          </ActionButton>
          <DimText>{recPage}</DimText>
          <ActionButton disabled={devBusy} onClick={() => recPageMove(1)}>
            Next
            <Icon name="chevron-right" size={12} />
          </ActionButton>
        </div>
        {recSelectedRecord && (
          <DimText>
            #{String(recSelectedRecord.id)} selected from {modelSelected || '—'}
          </DimText>
        )}
      </Card>

      <Card
        title="Cron Jobs"
        actions={
          <>
            <TextInput
              placeholder="Filter crons…"
              value={cronQ}
              onChange={(e) => setCronQ(e.target.value)}
              aria-label="Filter cron jobs"
            />
            <ActionButton disabled={devBusy} onClick={cronRefresh}>
              Refresh
            </ActionButton>
          </>
        }
      >
        <LineList
          lines={
            cronQ.trim()
              ? crons.filter((c) => c.toLowerCase().includes(cronQ.trim().toLowerCase()))
              : crons
          }
          maxHeight={150}
        />
        {!crons.length && <EmptyState text={cronEmpty} />}
        {Boolean(crons.length) &&
          cronQ.trim() &&
          !crons.some((c) => c.toLowerCase().includes(cronQ.trim().toLowerCase())) && (
            <EmptyState text="No cron jobs match the filter." />
          )}
      </Card>

      <Card
        title="Launch & editors"
        actions={
          <ActionButton
            disabled={devBusy || Boolean(editors && !editors.code)}
            title={editors && !editors.code ? 'VS Code was not found on PATH' : undefined}
            onClick={() =>
              void runDev(async () => {
                const res = await api.devtools.open_editor(iid, 'code')
                if (!res.ok) err(res.message)
              })
            }
          >
            Open in VS Code
          </ActionButton>
        }
        menu={[
          {
            label: editors && !editors.cursor ? 'Open in Cursor (not on PATH)' : 'Open in Cursor',
            disabled: devBusy || Boolean(editors && !editors.cursor),
            onClick: () =>
              void runDev(async () => {
                const res = await api.devtools.open_editor(iid, 'cursor')
                if (!res.ok) err(res.message)
              }),
          },
          {
            label: 'Generate launch.json',
            disabled: devBusy,
            onClick: () =>
              void runDev(async () => {
                const res = await api.devtools.launch_json(iid)
                if (!res.ok) err(res.message)
              }),
          },
        ]}
      />

      <Card
        title="Odoo Shell (PTY REPL)"
        actions={
          <>
            <ActionButton primary disabled={shellRunning || devBusy} onClick={shellStart}>
              Start shell
            </ActionButton>
            <ActionButton disabled={!shellRunning || devBusy} onClick={shellStop}>
              Stop
            </ActionButton>
            <ActionButton
              disabled={!shellLines.length}
              title="Copy the shell output to the clipboard"
              onClick={() =>
                void copyWithToast(shellLines.join('\n'), `${shellLines.length} shell lines`)
              }
            >
              Copy output
            </ActionButton>
          </>
        }
      >
        <DimText>{shellStatus}</DimText>
        <div className="shell-out">
          <LineList lines={shellLines} mono maxHeight={280} />
        </div>
        <div className="btn-row">
          <TextInput
            placeholder="Type Python to run in the shell… (Enter sends)"
            value={shellIn}
            onChange={(e) => setShellIn(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') shellSend()
            }}
          />
          <ActionButton disabled={!shellRunning || devBusy} onClick={shellSend}>
            Send
          </ActionButton>
        </div>
      </Card>

      <Card
        title="Dev Mode Watch"
        actions={
          <ActionButton disabled={devBusy} onClick={watchToggle}>
            {watchOn ? 'Stop watch' : 'Start watch'}
          </ActionButton>
        }
      >
        <DimText>{watchMsg}</DimText>
        <DimText>
          Watches custom_addons + community/enterprise roots for .py/.xml/.js/.css/.csv/.po changes
          — one automatic restart 0.8s after edits settle.
        </DimText>
      </Card>

      <Card
        title="Module tests"
        actions={
          <ActionButton
            primary
            disabled={devBusy || busy}
            title="Never runs on the primary database"
            onClick={testRun}
          >
            Run tests
          </ActionButton>
        }
      >
        <div className="btn-row">
          <TextInput
            placeholder="module name"
            value={testModule}
            onChange={(e) => setTestModule(e.target.value)}
          />
          <TextInput
            placeholder="test database (never primary!)"
            value={testDb}
            onChange={(e) => setTestDb(e.target.value)}
          />
        </div>
      </Card>

    </div>
  )
}

function intId(record: Dict): number | null {
  const raw = record.id
  const n = typeof raw === 'number' ? raw : Number.parseInt(String(raw ?? ''), 10)
  return Number.isFinite(n) ? n : null
}
