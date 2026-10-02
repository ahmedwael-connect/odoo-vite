/**
 * Filterable selection list — React port of ui_slint/selection_list.slint:
 * rows (id/title/badge/checked), optional filter box, multi-check support,
 * counts line, header rows. Selection is id-based (never index).
 */

import { useMemo, useState, type ReactNode } from 'react'
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

  return (
    <div className="sel-list-wrap">
      {showFilter && (
        <TextInput
          placeholder="Filter…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
      )}
      <div className="sel-list" style={maxHeight ? { maxHeight } : undefined}>
        {visible.map((row) =>
          row.header ? (
            <div key={row.id} className="sel-row header">
              {row.title}
            </div>
          ) : (
            <div
              key={row.id}
              className={`sel-row ${row.id === selectedId ? 'selected' : ''}`}
              onClick={() => onPick?.(row.id)}
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
