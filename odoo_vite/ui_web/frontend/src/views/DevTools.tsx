/**
 * DevTools — RPC connection, model inspector, paged record browser,
 * cron jobs, launch/editors, Odoo shell, module tests.
 * Port of devtools.slint + bridge _on_dev_action/_paint_* flows.
 */

import { useCallback, useEffect, useState } from 'react'
import { getApi } from '../bridge'
import { Modal, useConfirm, useTypedConfirm, useProgressRun } from '../components/dialog'
import { SelectionList, type SelRow } from '../components/selection'
import {
  ActionButton,
  Card,
  DimText,
  EmptyState,
  LineList,
  PasswordInput,
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

  const [shellRunning, setShellRunning] = useState(false)
  const [shellStatus, setShellStatus] = useState('Shell not running.')
  const [shellLines, setShellLines] = useState<string[]>([])
  const [shellIn, setShellIn] = useState('')

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
    return () => {
      offRpc()
      offModels()
      offMeta()
      offRecords()
      offCrons()
    }
  }, [currentId, api])

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
      <Card title="RPC connection">
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
          <ActionButton primary disabled={devBusy} onClick={rpcConnect}>
            Connect
          </ActionButton>
        </div>
        <DimText>{rpcStatus}</DimText>
      </Card>

      <Card title="Model Inspector">
        <SelectionList
          rows={modelRows}
          selectedId={modelSelected}
          onPick={pickModel}
          showFilter
          maxHeight={220}
          counts={`${models.length} model(s)`}
          emptyState={<EmptyState text={modelsEmpty} />}
        />
        <div className="btn-row">
          <ActionButton disabled={devBusy} onClick={reloadModels}>
            Reload models
          </ActionButton>
        </div>
        <DimText>{metaLine}</DimText>
      </Card>

      <Card title="Record Browser">
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
            ◀ Prev
          </ActionButton>
          <DimText>{recPage}</DimText>
          <ActionButton disabled={devBusy} onClick={() => recPageMove(1)}>
            Next ▶
          </ActionButton>
        </div>
        <div className="btn-row">
          <ActionButton disabled={devBusy} onClick={recNew}>
            New…
          </ActionButton>
          <ActionButton disabled={devBusy || !recSelected} onClick={recEdit}>
            Edit…
          </ActionButton>
          <ActionButton
            danger
            disabled={devBusy || !recSelected}
            title="Permanently deletes the record — no undo"
            onClick={recDelete}
          >
            Delete…
          </ActionButton>
        </div>
        {recSelectedRecord && (
          <DimText>
            #{String(recSelectedRecord.id)} selected from {modelSelected || '—'}
          </DimText>
        )}
      </Card>

      <Card title="Cron Jobs">
        <div className="btn-row">
          <ActionButton disabled={devBusy} onClick={cronRefresh}>
            Refresh
          </ActionButton>
        </div>
        <LineList lines={crons} maxHeight={150} />
        {!crons.length && <EmptyState text={cronEmpty} />}
      </Card>

      <Card title="Launch & editors">
        <div className="btn-row">
          <ActionButton
            disabled={devBusy}
            onClick={() =>
              void runDev(async () => {
                const res = await api.devtools.launch_json(iid)
                if (!res.ok) err(res.message)
              })
            }
          >
            Generate launch.json
          </ActionButton>
          <ActionButton
            disabled={devBusy}
            onClick={() =>
              void runDev(async () => {
                const res = await api.devtools.open_editor(iid, 'code')
                if (!res.ok) err(res.message)
              })
            }
          >
            Open in VS Code
          </ActionButton>
          <ActionButton
            disabled={devBusy}
            onClick={() =>
              void runDev(async () => {
                const res = await api.devtools.open_editor(iid, 'cursor')
                if (!res.ok) err(res.message)
              })
            }
          >
            Open in Cursor
          </ActionButton>
        </div>
      </Card>

      <Card title="Odoo Shell (PTY REPL)">
        <div className="btn-row">
          <ActionButton primary disabled={shellRunning || devBusy} onClick={shellStart}>
            Start shell
          </ActionButton>
          <ActionButton disabled={!shellRunning || devBusy} onClick={shellStop}>
            Stop
          </ActionButton>
          <DimText>{shellStatus}</DimText>
        </div>
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

      <DimText>
        Dev Mode Watch arrives in a later sprint (needs a file-watch design — no watcher in the
        web shell yet).
      </DimText>

      <Card title="Module tests">
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
          <ActionButton
            primary
            disabled={devBusy || busy}
            title="Never runs on the primary database"
            onClick={testRun}
          >
            Run tests
          </ActionButton>
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
