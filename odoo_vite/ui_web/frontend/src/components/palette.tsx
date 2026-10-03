/**
 * Command palette (Ctrl/Cmd+K): fuzzy search over navigation + core
 * actions. Substring beats subsequence; results capped; Arrow/Enter/Esc
 * on the input, click/mouse-move also work. Focus returns to the opener.
 */

import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react'
import { Icon, type IconName } from './icons'

export interface Command {
  id: string
  label: string
  group: string
  icon?: IconName
  hint?: string
  keywords?: string
  run: () => void
}

/** Score a command against the query: 0 = no match, higher = better. */
function score(query: string, cmd: Command): number {
  if (!query.trim()) return 1
  const hay = `${cmd.label} ${cmd.keywords ?? ''} ${cmd.group}`.toLowerCase()
  const needle = query.trim().toLowerCase()
  const at = hay.indexOf(needle)
  if (at !== -1) return 200 - at
  // subsequence fallback (fuzzy): every char in order
  let hi = 0
  let total = 0
  for (const ch of needle) {
    const idx = hay.indexOf(ch, hi)
    if (idx === -1) return 0
    total += idx === hi ? 2 : 1
    hi = idx + 1
  }
  return total
}

export function CommandPalette({
  commands,
  onClose,
}: {
  commands: Command[]
  onClose: () => void
}) {
  const [query, setQuery] = useState('')
  const [idx, setIdx] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const openerRef = useRef<HTMLElement | null>(null)

  const results = useMemo(() => {
    const scored = commands
      .map((c) => ({ c, s: score(query, c) }))
      .filter((x) => x.s > 0)
      .sort((a, b) => b.s - a.s)
    // 3.1.0 B12: the old cap of 12 hid commands forever — with an empty
    // query every score ties, so anything past 12 was unreachable.
    return scored.slice(0, 40).map((x) => x.c)
  }, [commands, query])

  useEffect(() => {
    openerRef.current = document.activeElement as HTMLElement | null
    inputRef.current?.focus()
    return () => openerRef.current?.focus?.()
  }, [])

  // 3.1.0 B12: Escape must close the palette even when the input lost
  // focus (clicking the overlay padding blurs it).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onClose()
      }
    }
    document.addEventListener('keydown', onKey, true)
    return () => document.removeEventListener('keydown', onKey, true)
  }, [onClose])

  useEffect(() => {
    setIdx(0)
  }, [query])

  const run = (cmd: Command) => {
    onClose()
    cmd.run()
  }

  const onInputKeyDown = (e: ReactKeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setIdx((i) => (results.length ? (i + 1) % results.length : 0))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setIdx((i) => (results.length ? (i - 1 + results.length) % results.length : 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const cmd = results[idx]
      if (cmd) run(cmd)
    } else if (e.key === 'Escape') {
      e.preventDefault()
      onClose()
    } else if (e.key === 'Tab') {
      e.preventDefault() // focus stays in the input
    }
  }

  return (
    <div
      className="palette-overlay"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="palette" role="dialog" aria-modal="true" aria-label="Command palette">
        <div className="palette-row">
          <Icon name="search" size={14} className="palette-search" />
          <input
            ref={inputRef}
            className="palette-input"
            placeholder="Type a command…"
            value={query}
            aria-label="Command"
            aria-controls="palette-list"
            aria-activedescendant={results.length ? `palette-item-${idx}` : undefined}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onInputKeyDown}
          />
          <kbd className="palette-esc">Esc</kbd>
        </div>
        <div className="palette-list" id="palette-list" role="listbox">
          {results.map((cmd, i) => (
            <div
              key={cmd.id}
              id={`palette-item-${i}`}
              role="option"
              aria-selected={i === idx}
              className={`palette-item ${i === idx ? 'active' : ''}`}
              onMouseEnter={() => setIdx(i)}
              onClick={() => run(cmd)}
            >
              <Icon name={cmd.icon ?? 'chevron-right'} size={14} />
              <span className="palette-label">{cmd.label}</span>
              <span className="palette-group dim-label">{cmd.group}</span>
              {cmd.hint && <kbd className="palette-hint">{cmd.hint}</kbd>}
            </div>
          ))}
          {results.length === 0 && (
            <div className="palette-empty dim-label">No matching command</div>
          )}
        </div>
      </div>
    </div>
  )
}
