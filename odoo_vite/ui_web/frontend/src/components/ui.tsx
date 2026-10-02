/**
 * Shared UI kit — the React translation of ui_slint/components.slint +
 * theme tokens (docs/design-system.md: spacing scale 4/8/12/16/24/32,
 * semantic color classes, no ad-hoc values).
 */

import type { ButtonHTMLAttributes, ReactNode } from 'react'

// ------------------------------------------------------------------ buttons

interface BtnProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  primary?: boolean
  danger?: boolean
}

export function Button({ primary, danger, className = '', ...rest }: BtnProps) {
  const variant = primary ? 'btn primary' : danger ? 'btn danger' : 'btn'
  return <button type="button" className={`${variant} ${className}`.trim()} {...rest} />
}

export function ActionButton({
  primary,
  danger,
  className = '',
  ...rest
}: BtnProps) {
  return <Button primary={primary} danger={danger} className={`btn action ${className}`.trim()} {...rest} />
}

// ------------------------------------------------------------------- layout

export function Card({ title, children, className = '' }: { title?: string; children: ReactNode; className?: string }) {
  return (
    <section className={`ov-card ${className}`.trim()}>
      {title && <h3 className="heading">{title}</h3>}
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
  const cls =
    status === 'running' ? 'status-running' : status === 'error' ? 'status-error' : 'status-stopped'
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
}: {
  lines: string[]
  mono?: boolean
  maxHeight?: number
  className?: string
}) {
  if (lines.length === 0) return null
  return (
    <div
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
