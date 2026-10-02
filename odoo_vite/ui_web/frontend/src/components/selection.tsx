/**
 * Filterable selection list — React port of ui_slint/selection_list.slint:
 * rows (id/title/badge/checked), optional filter box, multi-check support,
 * counts line, header rows. Selection is id-based (never index).
 *
 * Keyboard: roving focus — ArrowUp/Down (and Home/End) move, Enter picks,
 * Space picks (or toggles the checkbox in multi mode); ArrowDown from the
 * filter box jumps into the rows.
 */

import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { Checkbox, TextInput } from './ui'

export interface SelRow {
  id: string
  title: string
  badge?: string
  checked?: boolean
  header?: boolean
}

export interface SelectionListProps {
  rows: SelRow[]
  selectedId?: string
  onPick?: (id: string) => void
  onToggle?: (id: string, checked: boolean) => void
  multi?: boolean
  showFilter?: boolean
  counts?: string
  emptyState?: ReactNode
  maxHeight?: number
}

export function SelectionList({
  rows,
  selectedId,
  onPick,
  onToggle,
  multi = false,
  showFilter = false,
  counts,
  emptyState,
  maxHeight,
}: SelectionListProps) {
  const [filter, setFilter] = useState('')
  const [activeId, setActiveId] = useState<string | null>(selectedId ?? null)
  const listRef = useRef<HTMLDivElement>(null)

  const visible = useMemo(() => {
    if (!showFilter || !filter.trim()) return rows
    const needle = filter.trim().toLowerCase()
    return rows.filter(
      (r) =>
        r.header ||
        r.title.toLowerCase().includes(needle) ||
        (r.badge ?? '').toLowerCase().includes(needle),
    )
  }, [rows, filter, showFilter])

  const items = visible.filter((r) => !r.header)

  // keep the roving stop on a visible row (selection wins, else first)
  useEffect(() => {
    if (items.some((r) => r.id === activeId)) return
    const next =
      items.find((r) => r.id === selectedId) ?? items[0] ?? null
    setActiveId(next ? next.id : null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, selectedId])

  const focusRow = (id: string) => {
    setActiveId(id)
    listRef.current
      ?.querySelector<HTMLElement>(`[data-sel-id="${CSS.escape(id)}"]`)
      ?.focus()
  }

  const onRowKeyDown = (e: KeyboardEvent<HTMLDivElement>, row: SelRow) => {
    const idx = items.findIndex((r) => r.id === row.id)
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      const next = items[Math.min(items.length - 1, idx + 1)]
      if (next) focusRow(next.id)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      const prev = items[Math.max(0, idx - 1)]
      if (prev) focusRow(prev.id)
    } else if (e.key === 'Home') {
      e.preventDefault()
      if (items[0]) focusRow(items[0].id)
    } else if (e.key === 'End') {
      e.preventDefault()
      const last = items[items.length - 1]
      if (last) focusRow(last.id)
    } else if (e.key === 'Enter') {
      e.preventDefault()
      onPick?.(row.id)
    } else if (e.key === ' ' || e.key === 'Spacebar') {
      e.preventDefault()
      if (multi) onToggle?.(row.id, !row.checked)
      else onPick?.(row.id)
    }
  }

  const onFilterKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key !== 'ArrowDown') return
    e.preventDefault()
    const first = items[0]
    if (first) focusRow(first.id)
  }

  return (
    <div className="sel-list-wrap">
      {showFilter && (
        <TextInput
          placeholder="Filter…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          onKeyDown={onFilterKeyDown}
        />
      )}
      <div
        className="sel-list"
        style={maxHeight ? { maxHeight } : undefined}
        ref={listRef}
      >
        {visible.map((row) =>
          row.header ? (
            <div key={row.id} className="sel-row header">
              {row.title}
            </div>
          ) : (
            <div
              key={row.id}
              data-sel-id={row.id}
              className={`sel-row ${row.id === selectedId ? 'selected' : ''}`}
              tabIndex={row.id === activeId ? 0 : -1}
              onClick={() => {
                setActiveId(row.id)
                onPick?.(row.id)
              }}
              onFocus={() => setActiveId(row.id)}
              onKeyDown={(e) => onRowKeyDown(e, row)}
            >
              {multi && (
                <Checkbox
                  label=""
                  checked={Boolean(row.checked)}
                  onClick={(e) => e.stopPropagation()}
                  onChange={(e) => onToggle?.(row.id, e.target.checked)}
                />
              )}
              <span className="sel-title">{row.title}</span>
              {row.badge && <span className="sel-badge">{row.badge}</span>}
            </div>
          ),
        )}
        {visible.length === 0 && emptyState}
      </div>
      {counts && <div className="sel-counts dim-label">{counts}</div>}
    </div>
  )
}
