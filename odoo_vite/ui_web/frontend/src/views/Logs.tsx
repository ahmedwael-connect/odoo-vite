/**
 * Logs — 1s tail poll (only while this tab is mounted = visible),
 * regex search (live: pattern/level changes re-run debounced), level
 * filter on the streaming tail, doctor findings, slow queries,
 * profiler flame graph. Port of logs.slint + bridge _on_log_action/_tail_tick.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { copyWithToast } from '../clipboard'
import { getApi } from '../bridge'
import { ActionButton, Card, DimText, EmptyState, LineList, Select, TextInput } from '../components/ui'
import { Banner } from '../components/widgets'
import { onEvent } from '../events'
import { useApp } from '../store'
import type { Dict } from '../types'

const LOG_LEVELS = ['All levels', 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
const PROFILE_DURATIONS = ['5s', '10s', '30s']
const LINE_CAP = 2000
const TAIL_CAP = 2000
// mirrors core/log_search.LINE_RE (ts + pid + level prefix) — used by the
// tail level filter; continuation lines (tracebacks etc.) inherit the level
// of the last parsed line so tracebacks stay visible under ERROR/CRITICAL.
const LEVEL_RE = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:,\d+)?\s+\d+\s+([A-Z]+)\s/

export default function Logs({ active = false }: { active?: boolean }) {
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
  const [opError, setOpError] = useState('')
  const [tailLevelIdx, setTailLevelIdx] = useState(0)

  const followRef = useRef(follow)
  followRef.current = follow
  const linesRef = useRef<string[]>([])
  linesRef.current = lines
  const tailRef = useRef<HTMLDivElement>(null)
  // live-search runner: assigned after doSearch below (post early-return),
  // invoked by the debounced criteria effect above it
  const searchRunnerRef = useRef<() => void>(() => {})

  const hasInstance = Boolean(current && currentId)
  const busy = busyOps > 0

  const api = getApi()

  // tail level filter (client-side; display only — merge/caps unaffected)
  const filteredLines = useMemo(() => {
    if (tailLevelIdx === 0) return lines
    const want = LOG_LEVELS[tailLevelIdx]
    const out: string[] = []
    let inherited: string | null = null
    for (const l of lines) {
      const m = LEVEL_RE.exec(l)
      if (m) inherited = m[1]
      if (inherited === want) out.push(l)
    }
    return out
  }, [lines, tailLevelIdx])

  // ------------------------------------------------------------- tail poll
  // Backend contract (ui_web/api.py LogsApi.tail): first poll per log path
  // returns {initial:true} + the window; later polls only the new bytes;
  // rotation returns {rotated:true} + a fresh window. Clear is view-only —
  // the follower offset is untouched, so cleared stays cleared.
  //
  // Per-instance reset (keeps tab round-trips from wiping the view).
  useEffect(() => {
    setLines([])
    setNote('')
    setOpError('')
  }, [currentId])

  // Poll only while this tab is active: with keep-mounted views a
  // hidden Logs tab would tail the file forever in the background.
  // Pause/resume is lossless — the follower offset stays server-side,
  // so resume re-serves every byte written while hidden.
  useEffect(() => {
    if (!currentId || !active) return

    const merge = (res: { lines: string[]; initial?: boolean; rotated?: boolean }) => {
      const refill = res.initial === true || res.rotated === true
      if (refill) {
        // initial window / rotation fill renders even while paused — paused
        // means "don't grow with new lines", not "show nothing"
        setLines(res.lines.map((l) => l.slice(0, LINE_CAP)))
        if (res.rotated) setNote('Log rotated/truncated — restarted from top')
        return
      }
      if (!followRef.current) return // paused: follower already consumed the bytes
      const incoming = res.lines.map((l) => l.slice(0, LINE_CAP))
      if (incoming.length === 0) return
      const prev = linesRef.current
      const next = prev.length === 0 ? incoming : [...prev, ...incoming]
      setLines(next.length > TAIL_CAP ? next.slice(next.length - TAIL_CAP) : next)
    }

    let stopped = false
    const tick = async () => {
      if (stopped) return
      try {
        const res = await api.logs.tail(currentId, 500)
        if (stopped) return
        if (!res.ok || !Array.isArray(res.lines)) {
          setNote(res.message)
          return
        }
        setNote((prevNote) =>
          prevNote.startsWith('Log rotated')
            ? prevNote
            : `Tailing ${current?.log_path ?? 'log'}`,
        )
        merge(res)
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
  }, [currentId, active, api, current?.log_path])

  // stick to bottom while following: the tail pane (.tail-lines) is its own
  // scroll container (style.css), so this is the pane, not the page.
  // Deps use filteredLines: a hidden (filtered-out) append doesn't move the
  // view, so it must not yank the scroll either.
  useEffect(() => {
    if (!follow) return
    const el = tailRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [filteredLines, follow])

  // live search: pattern/level changes re-run automatically (debounced) so
  // the filters visibly do something without pressing Search; empty pattern
  // clears stale results instead of erroring
  useEffect(() => {
    if (!currentId) return
    if (!search.trim()) {
      setSearchLines([])
      setSearchStatus('')
      return
    }
    const t = window.setTimeout(() => searchRunnerRef.current(), 400)
    return () => window.clearTimeout(t)
  }, [search, levelIdx, currentId])

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

  // every op failure surfaces visibly (the old void-run pattern swallowed
  // rejections into unhandled-promise silence — "clicked, nothing happened")
  const run = async (fn: () => Promise<unknown>, label: string) => {
    setBusyOps((n) => n + 1)
    setBusy(true)
    setOpError('')
    try {
      await fn()
    } catch (err) {
      setOpError(`${label} failed: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setBusyOps((n) => Math.max(0, n - 1))
      setBusy(false)
    }
  }

  const doSearch = () => {
    if (!search.trim()) {
      setSearchLines([])
      setSearchStatus('Type a regex pattern to search')
      return
    }
    void run(async () => {
      const level = LOG_LEVELS[levelIdx]
      const res = await api.logs.search(currentId!, search, level === 'All levels' ? null : level)
      if (!res.ok) setSearchStatus(res.message)
    }, 'Search')
  }
  searchRunnerRef.current = doSearch

  const doDoctor = () =>
    void run(async () => {
      const res = await api.logs.doctor(currentId!)
      if (!res.ok) setDoctorLines([res.message])
      setDoctorVisible(true)
    }, 'Doctor')

  const doSlow = () =>
    void run(async () => {
      const res = await api.logs.slow_refresh(currentId!)
      if (!res.ok) setSlowStatus(res.message)
    }, 'Slow queries')

  const doProfile = () => {
    const text = PROFILE_DURATIONS[profileIdx]
    const seconds = Number(text.replace(/\D/g, '')) || 10
    void run(async () => {
      const res = await api.logs.profile(currentId!, seconds)
      if (!res.ok) {
        setProfileSvg('')
        setSearchStatus(res.message)
      }
    }, 'Profile')
  }

  const disabledTitle = !hasInstance ? 'Select an instance first' : busy ? 'working…' : ''

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
        <ActionButton
          disabled={!filteredLines.length}
          title="Copy the visible tail lines to the clipboard"
          onClick={() =>
            void copyWithToast(filteredLines.join('\n'), `${filteredLines.length} tail lines`)
          }
        >
          Copy tail
        </ActionButton>
        <Select
          value={String(tailLevelIdx)}
          onChange={(e) => setTailLevelIdx(Number(e.target.value))}
          title="Filter the streaming tail by log level (display only)"
          aria-label="Tail level filter"
        >
          {LOG_LEVELS.map((lvl, i) => (
            <option key={lvl} value={i}>
              {lvl}
            </option>
          ))}
        </Select>
        <ActionButton disabled={!hasInstance || busy} title={disabledTitle || 'Scan the log for known failure signatures'} onClick={doDoctor}>
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
          title={disabledTitle || 'Record a py-spy flame graph of the running process'}
          onClick={doProfile}
        >
          Profile
        </ActionButton>
        {busy && <span className="busy-dot">working…</span>}
      </div>

      {hasInstance && !follow && <Banner kind="warn">Not following — toggle Follow to resume</Banner>}
      {note && <p className="dim-label">{note}</p>}
      {opError && <Banner kind="error">{opError}</Banner>}

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
          <ActionButton
            disabled={!hasInstance || busy}
            title={disabledTitle || 'Search the full log file (re-runs as you type)'}
            onClick={doSearch}
          >
            Search
          </ActionButton>
        </div>
        {searchStatus && <DimText>{searchStatus}</DimText>}
        <LineList lines={searchLines} mono maxHeight={170} />
        {doctorVisible && <LineList lines={doctorLines} mono maxHeight={170} />}
      </Card>

      {tailLevelIdx > 0 && lines.length > 0 && (
        <DimText>
          filter: {LOG_LEVELS[tailLevelIdx]} — {filteredLines.length} of {lines.length} lines
        </DimText>
      )}

      <div className="tail-wrap">
        <LineList lines={filteredLines} mono className="tail-lines" innerRef={tailRef} />
        {lines.length === 0 && <EmptyState text="No log lines yet." />}
        {lines.length > 0 && filteredLines.length === 0 && (
          <EmptyState text="No lines match the level filter." />
        )}
      </div>

      <Card
        title="Slow queries (pg_stat_statements)"
        actions={
          <ActionButton disabled={!hasInstance || busy} onClick={doSlow}>
            Refresh
          </ActionButton>
        }
      >
        <DimText>{slowStatus}</DimText>
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
