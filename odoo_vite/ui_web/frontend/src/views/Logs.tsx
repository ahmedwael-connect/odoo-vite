/**
 * Logs — 1s tail poll (only while this tab is mounted = visible),
 * regex search, doctor findings, slow queries, profiler flame graph.
 * Port of logs.slint + bridge _on_log_action/_tail_tick.
 */

import { useEffect, useRef, useState } from 'react'
import { getApi } from '../bridge'
import { ActionButton, Card, DimText, EmptyState, LineList, Select, TextInput } from '../components/ui'
import { onEvent } from '../events'
import { useApp } from '../store'
import type { Dict } from '../types'

const LOG_LEVELS = ['All levels', 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
const PROFILE_DURATIONS = ['5s', '10s', '30s']
const LINE_CAP = 2000
const TAIL_CAP = 2000

export default function Logs() {
  const { current, currentId, setBusy } = useApp()

  const [lines, setLines] = useState<string[]>([])
  const [note, setNote] = useState('')
  const [follow, setFollow] = useState(true)
  const [search, setSearch] = useState('')
  const [levelIdx, setLevelIdx] = useState(0)
  const [searchLines, setSearchLines] = useState<string[]>([])
  const [searchStatus, setSearchStatus] = useState('')
  const [doctorLines, setDoctorLines] = useState<string[]>([])
  const [doctorVisible, setDoctorVisible] = useState(false)
  const [slowLines, setSlowLines] = useState<string[]>([])
  const [slowStatus, setSlowStatus] = useState('')
  const [profileIdx, setProfileIdx] = useState(1)
  const [profileSvg, setProfileSvg] = useState('')
  const [busyOps, setBusyOps] = useState(0)

  const followRef = useRef(follow)
  followRef.current = follow
  const linesRef = useRef<string[]>([])
  linesRef.current = lines

  const hasInstance = Boolean(current && currentId)
  const busy = busyOps > 0

  const api = getApi()

  // ------------------------------------------------------------- tail poll
  useEffect(() => {
    if (!currentId) return
    setLines([])
    setNote('')

    const merge = (tail: string[]) => {
      const prev = linesRef.current
      const capped = tail.map((l) => l.slice(0, LINE_CAP))
      if (!followRef.current) return
      if (prev.length === 0) {
        setLines(capped)
        return
      }
      const last = prev[prev.length - 1]
      const idx = capped.lastIndexOf(last)
      if (idx === -1) {
        // rotated/truncated — restart from what's on disk now
        setLines(capped)
        setNote('Log rotated/truncated — restarted from top')
        return
      }
      const added = capped.slice(idx + 1)
      if (added.length === 0) return
      const next = [...prev, ...added]
      setLines(next.length > TAIL_CAP ? next.slice(next.length - TAIL_CAP) : next)
    }

    let stopped = false
    const tick = async () => {
      if (stopped) return
      try {
        const res = await api.logs.tail(currentId, 500)
        if (stopped) return
        if (!res.ok) {
          setNote(res.message)
          return
        }
        setNote((prevNote) =>
          prevNote.startsWith('Log rotated') ? prevNote : `Tailing ${current?.log_path ?? 'log'} (last ${res.lines.length} lines shown)`,
        )
        merge(res.lines)
      } catch {
        /* transient poll failure — next tick retries */
      }
    }
    void tick()
    const timer = window.setInterval(() => void tick(), 1000)
    return () => {
      stopped = true
      window.clearInterval(timer)
    }
  }, [currentId, api, current?.log_path])

  // ----------------------------------------------------------------- events
  useEffect(() => {
    if (!currentId) return
    const offSearch = onEvent('log-search', (p) => {
      if (p.instance_id !== currentId) return
      setSearchStatus(p.message)
      void api.logs.format_search(p.matches).then(setSearchLines)
    })
    const offDoctor = onEvent('log-doctor', (p) => {
      const d = p as Dict
      if (d.instance_id !== currentId) return
      const findings = (d.findings ?? []) as unknown[]
      void api.logs.format_doctor(findings).then((l) => {
        setDoctorLines(l)
        setDoctorVisible(l.length > 0)
      })
    })
    const offSlow = onEvent('log-slow', (p) => {
      if (p.instance_id !== currentId) return
      setSlowStatus(p.message)
      void api.logs.format_slow(p.rows).then(setSlowLines)
    })
    const offProfile = onEvent('profile-ready', (p) => {
      if (p.instance_id !== currentId) return
      setProfileSvg(p.ok ? p.svg ?? '' : '')
      if (!p.ok) setSearchStatus(p.message)
    })
    return () => {
      offSearch()
      offDoctor()
      offSlow()
      offProfile()
    }
  }, [currentId, api])

  if (!hasInstance) {
    return <p className="empty-state dim-label">Select an instance</p>
  }

  const run = async (fn: () => Promise<unknown>) => {
    setBusyOps((n) => n + 1)
    setBusy(true)
    try {
      await fn()
    } finally {
      setBusyOps((n) => Math.max(0, n - 1))
      setBusy(false)
    }
  }

  const doSearch = () =>
    void run(async () => {
      const level = LOG_LEVELS[levelIdx]
      const res = await api.logs.search(currentId!, search, level === 'All levels' ? null : level)
      if (!res.ok) setSearchStatus(res.message)
    })

  const doDoctor = () =>
    void run(async () => {
      const res = await api.logs.doctor(currentId!)
      if (!res.ok) setDoctorLines([res.message])
      setDoctorVisible(true)
    })

  const doSlow = () =>
    void run(async () => {
      const res = await api.logs.slow_refresh(currentId!)
      if (!res.ok) setSlowStatus(res.message)
    })

  const doProfile = () => {
    const text = PROFILE_DURATIONS[profileIdx]
    const seconds = Number(text.replace(/\D/g, '')) || 10
    void run(async () => {
      const res = await api.logs.profile(currentId!, seconds)
      if (!res.ok) {
        setProfileSvg('')
        setSearchStatus(res.message)
      }
    })
  }

  return (
    <div className="view logs-view">
      <div className="btn-row">
        <label className="checkbox inline">
          <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} />
          <span />
          Follow
        </label>
        <ActionButton disabled={!hasInstance} onClick={() => setLines([])}>
          Clear view
        </ActionButton>
        <ActionButton disabled={!hasInstance || busy} onClick={doDoctor}>
          Run Doctor
        </ActionButton>
        <Select value={String(profileIdx)} onChange={(e) => setProfileIdx(Number(e.target.value))}>
          {PROFILE_DURATIONS.map((d, i) => (
            <option key={d} value={i}>
              {d}
            </option>
          ))}
        </Select>
        <ActionButton
          disabled={!hasInstance || busy}
          title="Record a py-spy flame graph of the running process"
          onClick={doProfile}
        >
          Profile
        </ActionButton>
        {busy && <span className="busy-dot">working…</span>}
      </div>

      {hasInstance && !follow && <p className="warn">⏸ not following — toggle Follow to resume</p>}
      {note && <p className="dim-label">{note}</p>}

      <Card title="Search (full file)">
        <div className="btn-row">
          <TextInput
            placeholder="regex pattern…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') doSearch()
            }}
          />
          <Select value={String(levelIdx)} onChange={(e) => setLevelIdx(Number(e.target.value))}>
            {LOG_LEVELS.map((lvl, i) => (
              <option key={lvl} value={i}>
                {lvl}
              </option>
            ))}
          </Select>
          <ActionButton disabled={!hasInstance || busy} onClick={doSearch}>
            Search
          </ActionButton>
        </div>
        {searchStatus && <DimText>{searchStatus}</DimText>}
        <LineList lines={searchLines} mono maxHeight={170} />
        {doctorVisible && <LineList lines={doctorLines} mono maxHeight={170} />}
      </Card>

      <div className="tail-wrap">
        <LineList lines={lines} mono className="tail-lines" />
        {lines.length === 0 && <EmptyState text="No log lines yet." />}
      </div>

      <Card title="Slow queries (pg_stat_statements)">
        <div className="split-row">
          <DimText>{slowStatus}</DimText>
          <ActionButton disabled={!hasInstance || busy} onClick={doSlow}>
            Refresh
          </ActionButton>
        </div>
        <LineList lines={slowLines} mono maxHeight={150} />
      </Card>

      {profileSvg && (
        <Card title="Profiler flame graph">
          <div className="flame-wrap" dangerouslySetInnerHTML={{ __html: profileSvg }} />
        </Card>
      )}
    </div>
  )
}
