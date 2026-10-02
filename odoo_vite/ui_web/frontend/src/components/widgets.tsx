/**
 * Quiet-Modern widgets: Tooltip (CSS bubble), Banner (persistent inline
 * feedback), OverflowMenu (kebab + menu), plus re-exports.
 *
 * Feedback model (docs/patterns.md): transient -> toast, persistent ->
 * Banner/StatusPill in place, empty -> EmptyState, loading -> Spinner.
 */

import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Icon } from './icons'

// ----------------------------------------------------------------- tooltip

/**
 * Hover/focus tooltip for icon-only controls. The label must also be
 * available to screen readers — pass `aria-label` on the inner control.
 */
export function Tooltip({ label, children }: { label: string; children: ReactNode }) {
  return (
    <span className="tip" data-tip={label}>
      {children}
    </span>
  )
}

// ------------------------------------------------------------------ banner

export type BannerKind = 'info' | 'ok' | 'warn' | 'error'

const BANNER_ICON = {
  info: 'info',
  ok: 'check',
  warn: 'alert',
  error: 'alert',
} as const

export function Banner({
  kind = 'info',
  children,
  action,
  onClose,
}: {
  kind?: BannerKind
  children: ReactNode
  action?: ReactNode
  onClose?: () => void
}) {
  return (
    <div
      className={`banner banner-${kind}`}
      role={kind === 'error' ? 'alert' : 'status'}
    >
      <Icon name={BANNER_ICON[kind]} size={14} className="banner-icon" />
      <span className="banner-text">{children}</span>
      {action && <span className="banner-action">{action}</span>}
      {onClose && (
        <button
          type="button"
          className="banner-x"
          aria-label="Dismiss"
          onClick={onClose}
        >
          <Icon name="x" size={12} />
        </button>
      )}
    </div>
  )
}

// --------------------------------------------------------------- kebab menu

export interface MenuItem {
  label: string
  onClick: () => void
  danger?: boolean
  disabled?: boolean
}

/** Icon-only overflow menu: trigger + popup, Esc/outside-click dismiss. */
export function OverflowMenu({
  items,
  label = 'More actions',
}: {
  items: MenuItem[]
  label?: string
}) {
  const [open, setOpen] = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)
  const btnRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      // let an open menu eat Esc before a parent Modal reacts
      e.stopPropagation()
      setOpen(false)
      btnRef.current?.focus()
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey, true)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey, true)
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    menuRef.current
      ?.querySelector<HTMLButtonElement>('button:not(:disabled)')
      ?.focus()
  }, [open])

  return (
    <div className="menu-wrap" ref={wrapRef}>
      <button
        type="button"
        ref={btnRef}
        className="btn icon"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={label}
        onClick={() => setOpen((o) => !o)}
      >
        <Icon name="more" />
      </button>
      {open && (
        <div className="menu" role="menu" ref={menuRef}>
          {items.map((it) => (
            <button
              key={it.label}
              type="button"
              role="menuitem"
              className={`menu-item${it.danger ? ' danger' : ''}`}
              disabled={it.disabled}
              onClick={() => {
                setOpen(false)
                it.onClick()
              }}
            >
              {it.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
