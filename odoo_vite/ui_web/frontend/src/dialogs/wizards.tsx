/**
 * New-instance (Create) and Adopt-existing wizards.
 * Port of ui_slint CreateDriver / AdoptDriver page stacks.
 *
 * Create: Version → Syscheck → Details → Provision (streamed, cancel/retry)
 * Adopt:  Locate → Gaps → Run (registry-only, never writes files)
 */

import { useEffect, useState } from 'react'
import { getApi } from '../bridge'
import { Modal } from '../components/dialog'
import { SelectionList } from '../components/selection'
import {
  ActionButton,
  Checkbox,
  DimText,
  EmptyState,
  ErrorText,
  LineList,
  SectionHeader,
  TextInput,
} from '../components/ui'
import { onEvent, route } from '../events'
import { useApp } from '../store'
import type { Dict } from '../types'

// ------------------------------------------------------------------ create

export function CreateWizard({ onClose }: { onClose: () => void }) {
  const api = getApi()
  const { refresh } = useApp()

  const [page, setPage] = useState(0)
  const [branches, setBranches] = useState<string[]>([])
  const [branchStatus, setBranchStatus] = useState('Loading branches…')
  const [version, setVersion] = useState('')

  const [sysLines, setSysLines] = useState<string[]>([])
  const [sysChecks, setSysChecks] = useState<Dict[]>([])
  const [sysStatus, setSysStatus] = useState('')
  const [instOpId] = useState(() => `sysinst-${Date.now()}`)
  const [instRunning, setInstRunning] = useState(false)
  const [instLines, setInstLines] = useState<string[]>([])

  const [details, setDetails] = useState({
    name: '',
    port: 8069,
    dbUser: 'odoo',
    dbPass: '',
    plaintext: false,
    dbName: '',
  })
  const [detErr, setDetErr] = useState('')

  const [draft, setDraft] = useState<Dict | null>(null)
  const [opId] = useState(() => `create-${Date.now()}`)
  const [provLines, setProvLines] = useState<string[]>([])
  const [provRunning, setProvRunning] = useState(false)
  const [provDone, setProvDone] = useState(false)
  const [provFailed, setProvFailed] = useState(false)
  const [provStatus, setProvStatus] = useState('')

  // -------------------------------------------------------- branch loading
  const loadBranches = () => {
    setBranchStatus('Loading branches…')
    void api.wizards
      .load_branches()
      .then((res) => {
        if (Array.isArray(res)) {
          setBranches(res as string[])
          setBranchStatus(`${(res as string[]).length} branches`)
        }
      })
      .catch(() => setBranchStatus('Branch list failed'))
  }

  useEffect(() => {
    const off = onEvent('wiz-branches', (p) => {
      const list = (p.branches ?? []) as string[]
      setBranches(list)
      const msg = String(p.message ?? '')
      setBranchStatus(msg || (list.length ? `${list.length} branches` : 'No branches listed.'))
    })
    loadBranches()
    return off
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ------------------------------------------------------------ syscheck
  const paintSyscheck = (checks: Dict[]) => {
    setSysChecks(checks)
    setSysLines(checks.map((c) => `${c.ok ? '✅' : '⚠'} ${c.name ?? '?'}: ${c.detail ?? ''}`))
  }

  const loadSyscheck = () => {
    setSysStatus(`Checking requirements for Odoo ${version}…`)
    void api.wizards.run_syscheck(version).then((checks) => {
      if (Array.isArray(checks)) {
        paintSyscheck(checks as Dict[])
        setSysStatus('')
      }
    })
  }

  useEffect(() => {
    if (page !== 1) return
    const off = onEvent('wiz-syscheck', (p) => {
      const checks = (p.checks ?? []) as Dict[]
      paintSyscheck(checks)
      setSysStatus(String(p.message ?? ''))
    })
    loadSyscheck()
    return off
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, version])

  // ------------------------------------------------- install-missing stream
  useEffect(() => {
    if (!instRunning) return
    const offLine = onEvent('progress-line', (p) => {
      if (p.op_id !== instOpId) return
      setInstLines((prev) => [...prev, String(p.line ?? '')].slice(-500))
      setSysStatus(String(p.line ?? '').slice(-120))
    })
    const offDone = onEvent('progress-done', (p) => {
      if (p.op_id !== instOpId) return
      setInstRunning(false)
      setSysStatus('')
      loadSyscheck()
    })
    return () => {
      offLine()
      offDone()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [instRunning, instOpId])

  const installMissing = async () => {
    const missing = sysChecks.filter((c) => c.requirement && !c.ok).map((c) => String(c.name))
    if (missing.length === 0) return
    setInstLines([])
    setInstRunning(true)
    try {
      const res = await api.wizards.install_requirements(missing, instOpId)
      route({
        kind: 'message',
        payload: { text: res.message, level: res.ok ? 'info' : 'error' },
      })
    } catch (err) {
      route({ kind: 'message', payload: { text: String(err), level: 'error' } })
    } finally {
      setInstRunning(false)
      setSysStatus('')
      loadSyscheck()
    }
  }

  // --------------------------------------------------- provision streaming
  useEffect(() => {
    if (!provRunning) return
    const offLine = onEvent('progress-line', (p) => {
      if (p.op_id !== opId) return
      setProvLines((prev) => [...prev, p.line].slice(-500))
      setProvStatus(p.line.slice(-120))
    })
    return offLine
  }, [provRunning, opId])

  const detailsPayload = () => ({
    name: details.name.trim(),
    port: Number(details.port) || 0,
    db_user: details.dbUser.trim() || 'odoo',
    db_password: details.dbPass,
    plaintext: details.plaintext,
    db_name: details.dbName.trim(),
  })

  const beginProvision = async () => {
    const values = detailsPayload()
    setProvLines([])
    setProvStatus('Provisioning…')
    setProvRunning(true)
    setProvDone(false)
    setProvFailed(false)
    // 3.2.0 P1: draft build/refresh sits INSIDE the try — a rejection there
    // (bad port, disk error) used to escape the IIFE and strand the page
    // on "Provisioning…" with no error shown.
    try {
      const inst: Dict = draft
        ? ((await api.wizards.refresh_draft(draft, values, version)) as Dict)
        : ((await api.wizards.build_draft(values, version)) as Dict)
      setDraft(inst)
      const res = await api.wizards.provision(inst, values.plaintext, opId)
      setProvRunning(false)
      if (res.ok) {
        setProvDone(true)
        setProvStatus('Instance ready ✓')
        refresh()
        route({ kind: 'message', payload: { text: res.message, level: 'info' } })
      } else {
        setProvFailed(true)
        setProvStatus('Provision failed')
        setProvLines((prev) => [...prev, `ERROR: ${res.message}`].slice(-500))
      }
    } catch (err) {
      setProvRunning(false)
      setProvFailed(true)
      setProvStatus('Provision failed')
      setProvLines((prev) =>
        [...prev, `ERROR: ${err instanceof Error ? err.message : String(err)}`].slice(-500),
      )
    }
  }

  const next = () => {
    if (page === 0) {
      if (!version) {
        setBranchStatus('Pick a version branch first.')
        return
      }
      setPage(1)
    } else if (page === 1) {
      setPage(2) // advisory — warnings never block
    } else if (page === 2) {
      void (async () => {
        try {
          const err = await api.wizards.validate_details(detailsPayload())
          if (err) {
            setDetErr(err)
            return
          }
          setDetErr('')
          setPage(3)
          await beginProvision()
        } catch (err) {
          setDetErr(err instanceof Error ? err.message : String(err))
        }
      })()
    }
  }

  const back = () => {
    if (page > 0 && !provRunning) setPage(page - 1)
  }

  const cancelProv = () => {
    setProvStatus('Cancelling… (waiting on subprocess)')
    void api.wizards.cancel(opId)
  }

  const discard = () => {
    void (async () => {
      if (draft) await api.wizards.discard_draft(String(draft.id ?? ''))
      onClose()
    })()
  }

  const footer = (
    <>
      {page < 3 && page > 0 && (
        <ActionButton onClick={back} disabled={provRunning}>
          Back
        </ActionButton>
      )}
      <ActionButton onClick={onClose} disabled={provRunning}>
        {page === 3 ? 'Close' : 'Cancel'}
      </ActionButton>
      {page < 2 && <ActionButton primary onClick={next}>Next</ActionButton>}
      {page === 2 && (
        <ActionButton primary onClick={next}>
          Create
        </ActionButton>
      )}
      {page === 3 && (
        <>
          {provRunning && <ActionButton danger onClick={cancelProv}>Cancel provision</ActionButton>}
          {provFailed && !provRunning && (
            <ActionButton primary onClick={() => void beginProvision()}>
              Retry
            </ActionButton>
          )}
          {(provFailed || provDone) && !provRunning && (
            <ActionButton danger onClick={discard}>
              Discard draft
            </ActionButton>
          )}
        </>
      )}
    </>
  )

  return (
    <Modal title={`New instance — page ${page + 1} of 4`} onClose={onClose} width={640} footer={footer}>
      {page === 0 && (
        <>
          <SectionHeader text="Version (odoo branch)" />
          <SelectionList
            rows={branches.map((b) => ({ id: b, title: b }))}
            selectedId={version}
            onPick={(id) => setVersion(id)}
            showFilter
            maxHeight={300}
            counts={`${branches.length} branches`}
            emptyState={<EmptyState text="No branches — press Refresh." />}
          />
          <div className="btn-row">
            <ActionButton onClick={loadBranches}>Refresh branches</ActionButton>
          </div>
          <DimText>{branchStatus}</DimText>
        </>
      )}

      {page === 1 && (
        <>
          <SectionHeader text={`System check — Odoo ${version}`} />
          <LineList lines={sysLines} maxHeight={320} />
          <DimText>{sysStatus}</DimText>
          {!instRunning &&
            sysChecks.some((c) => c.requirement && !c.ok) && (
              <div className="btn-row">
                <ActionButton onClick={() => void installMissing()} title="apt install the missing rows above">
                  Install missing…
                </ActionButton>
              </div>
            )}
          {instRunning && <LineList lines={instLines} maxHeight={200} />}
          <DimText>Warnings never block — review and continue.</DimText>
        </>
      )}

      {page === 2 && (
        <>
          <SectionHeader text="Instance details" />
          <div className="grid2">
            <label className="field">
              <span className="field-label">Instance name</span>
              <TextInput
                value={details.name}
                onChange={(e) => setDetails((d) => ({ ...d, name: e.target.value }))}
              />
            </label>
            <label className="field">
              <span className="field-label">Port</span>
              <TextInput
                value={String(details.port)}
                onChange={(e) => setDetails((d) => ({ ...d, port: Number(e.target.value) || 0 }))}
              />
            </label>
            <label className="field">
              <span className="field-label">DB user</span>
              <TextInput
                value={details.dbUser}
                onChange={(e) => setDetails((d) => ({ ...d, dbUser: e.target.value }))}
              />
            </label>
            <label className="field">
              <span className="field-label">Database name</span>
              <TextInput
                value={details.dbName}
                onChange={(e) => setDetails((d) => ({ ...d, dbName: e.target.value }))}
              />
            </label>
          </div>
          <div className="btn-row">
            <label className="field" style={{ flex: 1 }}>
              <span className="field-label">DB password</span>
              <TextInput
                value={details.dbPass}
                onChange={(e) => setDetails((d) => ({ ...d, dbPass: e.target.value }))}
              />
            </label>
            <ActionButton
              onClick={() =>
                void api.wizards.generate_password().then((pw) =>
                  setDetails((d) => ({ ...d, dbPass: pw })),
                )
              }
            >
              Generate
            </ActionButton>
          </div>
          <Checkbox
            label="Store password in plaintext (no keyring)"
            checked={details.plaintext}
            onChange={(e) => setDetails((d) => ({ ...d, plaintext: e.target.checked }))}
          />
          <ErrorText text={detErr} />
        </>
      )}

      {page === 3 && (
        <>
          <SectionHeader text={provStatus || 'Provisioning…'} />
          <LineList lines={provLines} mono maxHeight={320} />
          {provDone && <EmptyState text="Instance ready ✓" />}
          {provRunning && <DimText>provision running — Cancel waits on subprocesses</DimText>}
        </>
      )}
    </Modal>
  )
}

// ------------------------------------------------------------------ adopt

interface GapRow {
  key: string
  caption: string
  status: string
  missing: boolean
  value: string
}

export function AdoptWizard({ onClose }: { onClose: () => void }) {
  const api = getApi()
  const { refresh } = useApp()

  const [page, setPage] = useState(0)
  const [name, setName] = useState('')
  const [conf, setConf] = useState('')
  const [community, setCommunity] = useState('')
  const [detected, setDetected] = useState('')
  const [locErr, setLocErr] = useState('')

  const [parsed, setParsed] = useState<Dict>({})
  const [rows, setRows] = useState<GapRow[]>([])
  const [gapValues, setGapValues] = useState<Dict>({})
  const [gapDb, setGapDb] = useState('')
  const [gapErr, setGapErr] = useState('')

  const [runStatus, setRunStatus] = useState('')
  const [done, setDone] = useState(false)

  const reparse = (c: string, m: string): Promise<Dict> => {
    if (!c.trim()) {
      setParsed({})
      setDetected('')
      return Promise.resolve({})
    }
    return api.wizards.parse_adopt_paths(c, m).then((info) => {
      setParsed((info.parsed ?? {}) as Dict)
      setDetected(String(info.detected ?? ''))
      return info as Dict
    })
  }

  const buildGapPage = (info: Dict) => {
    void (async () => {
      try {
        const parsedNow = (info.parsed ?? {}) as Dict
        const reportNow = (info.report ?? {}) as Dict
        const gapRes = (await api.wizards.gap_rows(parsedNow, reportNow)) as unknown
        // 3.2.0 P0: a non-array answer (error object) would crash the .map
        // render and the seed loop below.
        const gap = Array.isArray(gapRes) ? (gapRes as GapRow[]) : []
        setRows(gap)
        const seed: Dict = {}
        for (const r of gap) if (r.missing) seed[r.key] = r.value
        setGapValues(seed)
        if (!gapDb.trim()) {
          const suggested = await api.wizards.suggest_db_name(name)
          setGapDb(suggested)
        }
      } catch (err) {
        setGapErr(err instanceof Error ? err.message : String(err))
      }
    })()
  }

  const next = () => {
    if (page === 0) {
      void (async () => {
        try {
          const err = await api.wizards.validate_locate(name.trim(), conf, community)
          if (err) {
            setLocErr(err)
            return
          }
          setLocErr('')
          const info = await reparse(conf, community)
          setPage(1)
          buildGapPage(info)
        } catch (err) {
          setLocErr(err instanceof Error ? err.message : String(err))
        }
      })()
    } else if (page === 1) {
      if (!gapDb.trim()) {
        setGapErr('Primary database is required.')
        return
      }
      setGapErr('')
      setPage(2)
      runAdopt()
    }
  }

  const runAdopt = () => {
    void (async () => {
      setRunStatus(`Adopting ${name.trim()}…`)
      setDone(false)
      try {
        const overrides = await api.wizards.build_adopt_overrides(parsed, gapValues, gapDb.trim())
        const res = await api.wizards.adopt_run(name.trim(), conf, community, overrides)
        if (res.ok) {
          setDone(true)
          setRunStatus('Adopted ✓ (no files touched)')
          refresh()
          route({ kind: 'message', payload: { text: res.message, level: 'info' } })
        } else {
          setDone(true)
          setRunStatus(`Adopt failed: ${res.message}`)
        }
      } catch (err) {
        setDone(true)
        setRunStatus(`Adopt failed: ${err instanceof Error ? err.message : String(err)}`)
      }
    })()
  }

  const footer = (
    <>
      {page > 0 && (
        <ActionButton onClick={() => setPage(page - 1)} disabled={page === 2 && !done}>
          Back
        </ActionButton>
      )}
      <ActionButton onClick={onClose}>Close</ActionButton>
      {page === 0 && (
        <ActionButton primary onClick={next}>
          Next
        </ActionButton>
      )}
      {page === 1 && (
        <ActionButton primary onClick={next}>
          Adopt
        </ActionButton>
      )}
    </>
  )

  return (
    <Modal title={`Adopt existing — page ${page + 1} of 3`} onClose={onClose} width={640} footer={footer}>
      {page === 0 && (
        <>
          <SectionHeader text="Locate the existing install" />
          <label className="field">
            <span className="field-label">Instance name</span>
            <TextInput value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <div className="btn-row">
            <TextInput
              placeholder="odoo.conf path"
              value={conf}
              onChange={(e) => {
                setConf(e.target.value)
                reparse(e.target.value, community)
              }}
            />
            <ActionButton
              onClick={() =>
                void api.app.pick_file('odoo.conf', 'open', 'conf').then((r) => {
                  if (r.ok && r.path) {
                    setConf(r.path)
                    reparse(r.path, community)
                  }
                })
              }
            >
              Browse…
            </ActionButton>
          </div>
          <div className="btn-row">
            <TextInput
              placeholder="Odoo community checkout"
              value={community}
              onChange={(e) => {
                setCommunity(e.target.value)
                reparse(conf, e.target.value)
              }}
            />
            <ActionButton
              onClick={() =>
                void api.app.pick_dir('Odoo community checkout').then((r) => {
                  if (r.ok && r.path) {
                    setCommunity(r.path)
                    reparse(conf, r.path)
                  }
                })
              }
            >
              Browse…
            </ActionButton>
          </div>
          <DimText>{detected || 'Detected version appears as you fill the paths.'}</DimText>
          <ErrorText text={locErr} />
        </>
      )}

      {page === 1 && (
        <>
          <SectionHeader text="Gaps — nothing is written until Adopt" />
          <label className="field">
            <span className="field-label">Primary database</span>
            <TextInput value={gapDb} onChange={(e) => setGapDb(e.target.value)} />
          </label>
          {rows.map((r) => (
            <div key={r.key} className="gap-row">
              <span className="field-label">{r.caption}</span>
              <span className={r.missing ? 'gap-missing' : 'dim-label'}>{r.status}</span>
              {r.missing && (
                <TextInput
                  value={String(gapValues[r.key] ?? '')}
                  onChange={(e) => setGapValues((v) => ({ ...v, [r.key]: e.target.value }))}
                />
              )}
            </div>
          ))}
          <ErrorText text={gapErr} />
        </>
      )}

      {page === 2 && (
        <>
          <SectionHeader text={runStatus || 'Adopting…'} />
          {done && <EmptyState text={runStatus} warn={runStatus.startsWith('Adopt failed')} />}
          {!done && <DimText>Adopt is registry-only — no files are touched.</DimText>}
        </>
      )}
    </Modal>
  )
}
