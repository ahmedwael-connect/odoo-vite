/**
 * Shared UI kit — the React translation of ui_slint/components.slint +
 * theme tokens (docs/design-system.md: spacing scale 4/8/12/16/24/32,
 * semantic color classes, no ad-hoc values).
 */

import type { ButtonHTMLAttributes, ReactNode, Ref } from 'react'
import { OverflowMenu, type MenuItem } from './widgets'

// ------------------------------------------------------------------ buttons

interface BtnProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  primary?: boolean
  danger?: boolean
  ghost?: boolean
  size?: 'sm' | 'md'
  /** Square icon-only variant (pair with aria-label). */
  icon?: boolean
  /** Shows an inline spinner and blocks the button (aria-busy). */
  loading?: boolean
}

export function Button({
  primary,
  danger,
  ghost,
  size = 'md',
  icon,
  loading,
  className = '',
  disabled,
  children,
  ...rest
}: BtnProps) {
  const variant = primary ? 'btn primary' : danger ? 'btn danger' : ghost ? 'btn ghost' : 'btn'
  const cls = [variant, size === 'sm' ? 'sm' : '', icon ? 'icon' : '', className]
    .filter(Boolean)
    .join(' ')
  return (
    <button
      type="button"
      className={cls}
      aria-busy={loading || undefined}
      disabled={disabled || loading}
      {...rest}
    >
      {loading && <span className="spinner" aria-hidden />}
      {children}
    </button>
  )
}

export function ActionButton({
  primary,
  danger,
  ghost,
  size,
  loading,
  className = '',
  ...rest
}: BtnProps) {
  return (
    <Button
      primary={primary}
      danger={danger}
      ghost={ghost}
      size={size}
      loading={loading}
      className={`btn action ${className}`.trim()}
      {...rest}
    />
  )
}

// ------------------------------------------------------------------- layout

/**
 * Card with optional header actions (docs/patterns.md action IA): the card's
 * primary action sits right-aligned in the header, secondaries live in the
 * header overflow menu. Content stays in the body.
 */
export function Card({
  title,
  children,
  className = '',
  actions,
  menu,
}: {
  title?: string
  children?: ReactNode
  className?: string
  /** Header buttons; the primary one first. */
  actions?: ReactNode
  /** Header overflow menu entries (secondary actions). */
  menu?: MenuItem[]
}) {
  const head = title || actions || menu
  return (
    <section className={`ov-card ${className}`.trim()}>
      {head && (
        <div className="card-head">
          {title && <h3 className="heading">{title}</h3>}
          {(actions || menu) && (
            <div className="card-actions">
              {actions}
              {menu && menu.length > 0 && (
                <OverflowMenu items={menu} label={`${title ?? 'Card'} actions`} />
              )}
            </div>
          )}
        </div>
      )}
      {children}
    </section>
  )
}

export function SectionHeader({ text }: { text: string }) {
  return <div className="section-header">{text}</div>
}

export function EmptyState({ text, warn }: { text: string; warn?: boolean }) {
  if (!text) return null
  return <p className={warn ? 'empty-state warning' : 'empty-state dim-label'}>{text}</p>
}

export function ErrorText({ text }: { text: string }) {
  if (!text) return null
  return <p className="error">{text}</p>
}

export function DimText({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <p className={`dim-label ${className}`.trim()}>{children}</p>
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="spinner-wrap">
      <span className="spinner" aria-hidden />
      {label && <span className="dim-label">{label}</span>}
    </span>
  )
}

// -------------------------------------------------------------- status pill

export function StatusPill({ status }: { status: string }) {
  const s = (status || 'unknown').toLowerCase()
  const cls =
    s === 'running'
      ? 'status-running'
      : s === 'error' || s === 'failed'
        ? 'status-error'
        : s === 'draft' || s === 'installing' || s === 'updating' || s === 'uninstalling'
          ? 'status-draft'
          : 'status-stopped'
  return <span className={`pill ${cls}`}>{status || 'unknown'}</span>
}

// ------------------------------------------------------------------- forms

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
    </label>
  )
}

export function TextInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input type="text" className="input" {...props} />
}

export function PasswordInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input type="password" className="input" {...props} />
}

export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className="input select" {...props} />
}

export function Checkbox({ label, ...rest }: { label: string } & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <label className="checkbox">
      <input type="checkbox" {...rest} />
      <span>{label}</span>
    </label>
  )
}

export function SpinInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input type="number" className="input spin" {...props} />
}

// --------------------------------------------------------------- line list

export function LineList({
  lines,
  mono,
  maxHeight,
  className = '',
  innerRef,
}: {
  lines: string[]
  mono?: boolean
  maxHeight?: number
  className?: string
  innerRef?: Ref<HTMLDivElement>
}) {
  if (lines.length === 0) return null
  return (
    <div
      ref={innerRef}
      className={`linelist ${mono ? 'monospace' : ''} ${className}`.trim()}
      style={maxHeight ? { maxHeight } : undefined}
    >
      {lines.map((line, i) => (
        <div key={i} className="linelist-line">
          {line}
        </div>
      ))}
    </div>
  )
}

// -------------------------------------------------------------- text elide

/** Long values (paths): middle-elide with full text in title (patterns.md). */
export function ElidePath({ path }: { path: string }) {
  if (!path) return null
  return (
    <span className="elide-path monospace" title={path}>
      {path}
    </span>
  )
}
