/**
 * Modules — search/filter/checked ops with streamed progress.
 * Port of ui_slint/modules.slint + bridge _on_mod_action.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { getApi } from '../bridge'
import { Modal, useConfirm, useConfirmBackup, useProgressRun } from '../components/dialog'
import {
  ActionButton,
  Card,
  EmptyState,
  LineList,
  SectionHeader,
  Select,
  TextInput,
} from '../components/ui'
import { onEvent, route } from '../events'
import { useApp } from '../store'
import type { Dict, ModuleRow } from '../types'

const STATE_FILTERS = ['All', 'Installed', 'Upgradeable', 'Installable']

function DepsDialog({
  name,
  depends,
  requiredBy,
  onClose,
}: {
  name: string
  depends: string[]
  requiredBy: string[]
  onClose: () => void
}) {
  return (
    <Modal
      title={`Dependencies — ${name}`}
      onClose={onClose}
      width={520}
      footer={
        <ActionButton primary onClick={onClose}>
          Close
        </ActionButton>
      }
    >
      <SectionHeader text={`Depends on (${depends.length})`} />
      <LineList lines={depends} />
      {depends.length === 0 && <p className="dim-label">none</p>}
      <SectionHeader text={`Required by (${requiredBy.length})`} />
      <LineList lines={requiredBy} />
      {requiredBy.length === 0 && <p className="dim-label">none</p>}
    </Modal>
  )
}

function ScaffoldDialog({
  dbNames,
  defaultVersion,
  onClose,
  onInstall,
}: {
  dbNames: string[]
  defaultVersion: string
  onClose: () => void
  onInstall: (definition: Dict, dest: string, db: string) => void
}) {
  const api = getApi()
  const [tech, setTech] = useState('')
  const [pretty, setPretty] = useState('')
  const [version, setVersion] = useState(defaultVersion || '17.0')
  const [summary, setSummary] = useState('')
  const [author, setAuthor] = useState('')
  const [model, setModel] = useState('')
  const [fieldsText, setFieldsText] = useState('')
  const [dest, setDest] = useState('')
  const [db, setDb] = useState(dbNames[0] ?? '')
  const [error, setError] = useState('')

  const browse = async () => {
    const picked = await api.app.pick_dir('Module destination folder')
    if (picked.ok && picked.path) setDest(picked.path)
  }

  const save = async () => {
    const values = {
      tech,
      pretty,
      version,
      summary,
      author,
      model,
      fields_text: fieldsText,
      dest,
      db,
    }
    const err = await api.wizards.validate_scaffold(values, dbNames.length > 0)
    if (err) {
      setError(err)
      return
    }
    const definition = await api.wizards.build_scaffold_definition(values)
    onInstall(definition, dest, db)
  }

  return (
    <Modal
      title="New module"
      onClose={onClose}
      width={560}
      footer={
        <>
          <ActionButton onClick={onClose}>Cancel</ActionButton>
          <ActionButton primary onClick={() => void save()}>
            Generate & install
          </ActionButton>
        </>
      }
    >
      <div className="grid2">
        <label className="field">
          <span className="field-label">Technical name</span>
          <TextInput value={tech} onChange={(e) => setTech(e.target.value)} placeholder="my_module" />
        </label>
        <label className="field">
          <span className="field-label">Display name</span>
          <TextInput value={pretty} onChange={(e) => setPretty(e.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">Odoo version</span>
          <TextInput value={version} onChange={(e) => setVersion(e.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">Author</span>
          <TextInput value={author} onChange={(e) => setAuthor(e.target.value)} />
        </label>
      </div>
      <label className="field">
        <span className="field-label">Summary</span>
        <TextInput value={summary} onChange={(e) => setSummary(e.target.value)} />
      </label>
      <div className="grid2">
        <label className="field">
          <span className="field-label">Model (technical)</span>
          <TextInput value={model} onChange={(e) => setModel(e.target.value)} placeholder="my.model" />
        </label>
        <label className="field">
          <span className="field-label">Acceptance DB</span>
          <Select value={db} onChange={(e) => setDb(e.target.value)}>
            {dbNames.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </Select>
        </label>
      </div>
      <label className="field">
        <span className="field-label">Fields (one “name:type” per line)</span>
        <textarea
          className="input textarea mono"
          rows={4}
          value={fieldsText}
          onChange={(e) => setFieldsText(e.target.value)}
          placeholder={'code: char\namount: float'}
        />
      </label>
      <div className="btn-row">
        <TextInput placeholder="destination folder" value={dest} onChange={(e) => setDest(e.target.value)} />
        <ActionButton onClick={() => void browse()}>Browse…</ActionButton>
      </div>
      {error && <p className="error">{error}</p>}
    </Modal>
  )
}

// -------------------------------------------------------------------- view

export default function Modules() {
  const { current, currentId, setDialog, setBusy } = useApp()
  const confirm = useConfirm()
  const confirmBackup = useConfirmBackup()
  const runProgress = useProgressRun()

  const [modules, setModules] = useState<ModuleRow[]>([])
  const [cats, setCats] = useState<Record<string, string>>({})
  const [diff, setDiff] = useState<Dict>({})
  const [error, setError] = useState('')
  const [checked, setChecked] = useState<Set<string>>(new Set())
  const [selected, setSelected] = useState('')
  const [search, setSearch] = useState('')
  const [filterIdx, setFilterIdx] = useState(0)
  const [busy, setLocalBusy] = useState(false)

  const primary = current?.primary_db ?? ''
  const modDb = primary ? `on ${primary}` : 'no primary database'

  const paint = useCallback(
    async (mods: ModuleRow[], diffMap: Dict, errText: string) => {
      setModules(mods)
      setDiff(diffMap)
      setError(errText)
      try {
        setCats(await getApi().modules.state_categories(mods as unknown as Dict[]))
      } catch {
        setCats({})
      }
    },
    [],
  )

  // load on instance change; consume events
  useEffect(() => {
    if (!currentId) return
    setModules([])
    setCats({})
    setChecked(new Set())
    setSelected('')
    setError('Loading…')

    const offReady = onEvent('modules-ready', (p) => {
      if (p.instance_id !== currentId) return
      void paint(p.modules as unknown as ModuleRow[], p.diff as Dict, p.error ?? '')
    })
    const offDeps = onEvent('deps-ready', (p) => {
      if ((p as Dict).instance_id && (p as Dict).instance_id !== currentId) return
      const d = p as unknown as Dict & {
        name?: string
        depends?: string[]
        required_by?: string[]
      }
      setDialog(
        <DepsDialog
          name={d.name ?? ''}
          depends={d.depends ?? (d as Dict & { deps?: string[] }).deps ?? []}
          requiredBy={(d.required_by ?? (d as Dict & { required_by?: string[] }).required_by ?? []) as string[]}
          onClose={() => setDialog(null)}
        />,
      )
    })
    void getApi()
      .modules.refresh_modules(currentId)
      .then((res) => {
        if (!res.ok) setError(res.message)
      })
    return () => {
      offReady()
      offDeps()
    }
  }, [currentId, paint, setDialog])

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase()
    const want = STATE_FILTERS[filterIdx]
    return modules.filter((m) => {
      if (needle && !m.name.toLowerCase().includes(needle) && !(m.summary ?? '').toLowerCase().includes(needle))
        return false
      if (want !== 'All' && (cats[m.name] ?? '') !== want) return false
      return true
    })
  }, [modules, cats, search, filterIdx])

  if (!current || !currentId) {
    return <p className="empty-state dim-label">Select an instance</p>
  }

  const api = getApi()
  const checkedVisible = visible.filter((m) => checked.has(m.name)).length

  const routeErr = (text: string) => route({ kind: 'message', payload: { text, level: 'error' } })

  const refresh = async () => {
    setLocalBusy(true)
    setBusy(true)
    try {
      const res = await api.modules.refresh_modules(currentId)
      if (!res.ok) setError(res.message)
    } finally {
      setLocalBusy(false)
      setBusy(false)
    }
  }

  const previewFor = async (flag: 'install' | 'update' | 'uninstall', names: string[]) => {
    const res = await api.modules.preview_command(currentId, primary, flag, names)
    return res
  }

  const runOp = async (
    title: string,
    names: string[],
    kind: 'install' | 'update' | 'uninstall' | 'update-code',
  ) => {
    setLocalBusy(true)
    setBusy(true)
    try {
      const result = await runProgress(title, (opId) => {
        if (kind === 'install') return api.modules.install(currentId, names, opId)
        if (kind === 'update') return api.modules.update(currentId, names, opId)
        if (kind === 'uninstall') return api.modules.uninstall(currentId, names[0], opId)
        return api.modules.update_code(currentId, opId)
      })
      if (!result.ok) routeErr(result.message)
      return result
    } finally {
      setLocalBusy(false)
      setBusy(false)
      await refresh()
    }
  }

  const onAction = async (action: string) => {
    const checkedNames = [...checked]
    switch (action) {
      case 'refresh':
        await refresh()
        return
      case 'install':
      case 'update': {
        if (checkedNames.length === 0) return routeErr('Check at least one module')
        if (!primary) return routeErr('This instance has no primary database')
        const verb = action === 'install' ? 'Install' : 'Update'
        const cmd = await previewFor(action, checkedNames)
        const ok = await confirm({
          heading: `${verb} ${checkedNames.join(', ')} into "${primary}"?`,
          body: `Runs:\n${cmd}`,
          confirmLabel: verb,
        })
        if (ok) await runOp(`${verb} modules`, checkedNames, action)
        return
      }
      case 'uninstall': {
        if (!selected) return routeErr('Pick a module first')
        const cmd = await previewFor('uninstall', [selected])
        const ok = await confirm({
          heading: `Uninstall ${selected} from "${primary}"?`,
          body: `Runs:\n${cmd}`,
          confirmLabel: 'Uninstall',
          destructive: true,
        })
        if (ok) await runOp(`Uninstall ${selected}`, [selected], 'uninstall')
        return
      }
      case 'update-code': {
        if (current.status === 'running') return routeErr('Stop the instance first')
        const auto = current.auto_update_modules ?? []
        if (auto.length === 0) return routeErr('No auto-update modules configured')
        if (!primary) return routeErr('This instance has no primary database')
        const { ok, checked: doBackup } = await confirmBackup({
          heading: 'Update code for checked modules?',
          body:
            `git pull community, pip install, then -u ${auto.join(', ')}; instance stays stopped.\n` +
            `A backup of "${primary}" runs first unless you uncheck it.`,
          confirmLabel: 'Update code',
          destructive: true,
          checkboxLabel: `Back up "${primary}" before updating`,
        })
        if (!ok) return
        if (doBackup) {
          setLocalBusy(true)
          setBusy(true)
          try {
            const iso = new Date().toISOString()
            const stamp = `${iso.slice(0, 10).replace(/-/g, '')}-${iso.slice(11, 19).replace(/:/g, '')}`
            const dest = `${current.path}/pre-update-${primary}-${stamp}.dump`
            const res = await api.databases.backup_db(currentId, primary, dest)
            if (!res.ok) {
              routeErr(`Backup failed — update aborted: ${res.message}`)
              return
            }
            route({ kind: 'message', payload: { text: `Backup saved: ${dest}`, level: 'info' } })
          } finally {
            setLocalBusy(false)
            setBusy(false)
          }
        }
        await runOp('Update code', auto, 'update-code')
        return
      }
      case 'deps': {
        if (!selected) return routeErr('Pick a module first')
        const res = await api.modules.fetch_deps(currentId, selected)
        if (!res.ok) routeErr(res.message)
        return
      }
      case 'scaffold': {
        const dbNames = [primary, ...(current.tracked_dbs ?? [])].filter(Boolean)
        setDialog(
          <ScaffoldDialog
            dbNames={dbNames}
            defaultVersion={current.version}
            onClose={() => setDialog(null)}
            onInstall={(definition, dest, db) => {
              setDialog(null)
              void (async () => {
                setLocalBusy(true)
                setBusy(true)
                try {
                  const result = await runProgress('Scaffold module', (opId) =>
                    api.wizards.scaffold_install(definition, dest, currentId, db, opId),
                  )
                  if (!result.ok) routeErr(result.message)
                } finally {
                  setLocalBusy(false)
                  setBusy(false)
                  await refresh()
                }
              })()
            }}
          />,
        )
        return
      }
    }
  }

  return (
    <div className="view">
      <Card
        title="Modules"
        actions={
          <>
            <ActionButton
              primary
              disabled={busy || checked.size === 0}
              onClick={() => void onAction('install')}
            >
              Install Checked
            </ActionButton>
            <ActionButton disabled={busy || checked.size === 0} onClick={() => void onAction('update')}>
              Update Checked
            </ActionButton>
            <ActionButton
              disabled={busy}
              title="git pull + pip install + module update (stays stopped)"
              onClick={() => void onAction('update-code')}
            >
              Update Code…
            </ActionButton>
          </>
        }
        menu={[
          {
            label: 'Uninstall selected…',
            danger: true,
            disabled: busy || !selected,
            onClick: () => void onAction('uninstall'),
          },
          { label: 'Dependencies…', disabled: busy || !selected, onClick: () => void onAction('deps') },
          { label: 'New Module…', disabled: busy, onClick: () => void onAction('scaffold') },
        ]}
      >
        <p className="dim-label">{modDb}</p>
        <div className="btn-row">
          <TextInput
            placeholder="Filter by name or summary…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <Select value={String(filterIdx)} onChange={(e) => setFilterIdx(Number(e.target.value))}>
            {STATE_FILTERS.map((f, i) => (
              <option key={f} value={i}>
                {f}
              </option>
            ))}
          </Select>
          <ActionButton disabled={busy} onClick={() => void onAction('refresh')}>
            Refresh
          </ActionButton>
        </div>

        <div className="sel-list" style={{ maxHeight: 360 }}>
          {visible.map((m) => {
            const d = (diff as Record<string, { status?: string; note?: string } | undefined>)[m.name]
            const warn = d && d.status && d.status !== 'in-sync' && d.status !== null ? `  ⚠ ${d.note ?? d.status}` : ''
            return (
              <div
                key={m.name}
                className={`sel-row ${selected === m.name ? 'selected' : ''}`}
                onClick={() => setSelected(m.name)}
              >
                <label className="checkbox" onClick={(e) => e.stopPropagation()}>
                  <input
                    type="checkbox"
                    checked={checked.has(m.name)}
                    onChange={(e) =>
                      setChecked((prev) => {
                        const next = new Set(prev)
                        if (e.target.checked) next.add(m.name)
                        else next.delete(m.name)
                        return next
                      })
                    }
                  />
                  <span />
                </label>
                <span className="sel-title mono">
                  {m.name}
                  {warn}
                </span>
                <span className="sel-badge">{cats[m.name] ?? ''}</span>
              </div>
            )
          })}
          {visible.length === 0 && (
            <EmptyState
              text={error || (modules.length === 0 ? 'No modules loaded — select an instance with a primary database, then press Refresh.' : 'No modules match — adjust the filter.')}
            />
          )}
        </div>
        <div className="sel-counts dim-label">
          {checked.size ? `${checked.size} checked · ` : ''}
          {visible.length} of {modules.length} shown
          {checked.size > 0 && checkedVisible === 0 ? ' (checked hidden by filter)' : ''}
        </div>
      </Card>
    </div>
  )
}
