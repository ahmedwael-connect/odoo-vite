/**
 * Modal dialogs: generic Modal + the two confirmation tiers from
 * docs/patterns.md (light confirm / type-to-confirm) + streaming progress.
 *
 * Confirm helpers are promise-based so action handlers read linearly:
 *   if (!(await confirm({...}))) return;
 */

import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { getApi } from '../bridge'
import { onEvent } from '../events'
import { useApp } from '../store'
import { ActionButton, Button, LineList, TextInput } from './ui'
import { Icon } from './icons'

export interface ModalProps {
  title: string
  children?: ReactNode
  footer?: ReactNode
  onClose?: () => void
  width?: number
}

// Mount-order registry: only the topmost modal reacts to Escape or traps
// Tab. Ids are stable per Modal instance; effects push/pop in DOM order.
let modalSeq = 0
const modalStack: number[] = []

const FOCUSABLE =
  'a[href], button:not(:disabled), input:not(:disabled):not([type="hidden"]), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])'

export function Modal({ title, children, footer, onClose, width }: ModalProps) {
  const idRef = useRef<number>(0)
  if (idRef.current === 0) idRef.current = ++modalSeq
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const id = idRef.current
    modalStack.push(id)
    return () => {
      const i = modalStack.lastIndexOf(id)
      if (i !== -1) modalStack.splice(i, 1)
    }
  }, [])

  const isTop = () => modalStack[modalStack.length - 1] === idRef.current

  // focus: remember opener, focus the dialog (autofocus wins), restore back
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    const root = rootRef.current
    const raf = window.requestAnimationFrame(() => {
      if (!root) return
      const target =
        root.querySelector<HTMLElement>('[data-autofocus], [autofocus]') ?? root
      target.focus({ preventScroll: true })
    })
    return () => {
      window.cancelAnimationFrame(raf)
      opener?.focus?.({ preventScroll: true })
    }
  }, [])

  // Esc (capture): top modal only; an open menu inside it eats Esc first
  useEffect(() => {
    if (!onClose) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || !isTop()) return
      const t = e.target as HTMLElement | null
      if (t?.closest?.('.menu-wrap')) return
      e.preventDefault()
      e.stopPropagation()
      onClose()
    }
    document.addEventListener('keydown', onKey, true)
    return () => document.removeEventListener('keydown', onKey, true)
  }, [onClose])

  // Tab: cycle within the top modal
  const onTab = (e: React.KeyboardEvent) => {
    if (e.key !== 'Tab' || !isTop()) return
    const root = rootRef.current
    if (!root) return
    const nodes = [...root.querySelectorAll<HTMLElement>(FOCUSABLE)]
    if (nodes.length === 0) {
      e.preventDefault()
      return
    }
    const first = nodes[0]
    const last = nodes[nodes.length - 1]
    const active = document.activeElement
    if (e.shiftKey) {
      if (active === first || active === root || !root.contains(active)) {
        e.preventDefault()
        last.focus()
      }
    } else if (active === last || !root.contains(active)) {
      e.preventDefault()
      first.focus()
    }
  }

  return (
    <div
      className="modal-overlay"
      onMouseDown={(e) => e.target === e.currentTarget && onClose?.()}
    >
      <div
        ref={rootRef}
        className="modal"
        style={width ? { width } : undefined}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        onKeyDown={onTab}
      >
        <header className="modal-head">
          <h2 className="title">{title}</h2>
          {onClose && (
            <button type="button" className="modal-x" onClick={onClose} aria-label="Close">
              <Icon name="x" size={14} />
            </button>
          )}
        </header>
        <div className="modal-body">{children}</div>
        {footer && <footer className="modal-foot">{footer}</footer>}
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ confirm

export interface ConfirmOpts {
  heading: string
  body?: string
  confirmLabel?: string
  destructive?: boolean
  expected?: string
}

export interface TypedConfirmOpts extends ConfirmOpts {
  expected: string
}

function ConfirmView({
  opts,
  typed,
  onResult,
  onAbandon,
}: {
  opts: ConfirmOpts
  typed?: boolean
  onResult: (ok: boolean) => void
  /** 3.1.0 B14: settles the promise when the view unmounts without a
   * result (setDialog replaced the stack mid-await — the old behaviour
   * was an await that hung forever). Skipped on StrictMode's fake
   * unmount: the remount clears the pending timer first. */
  onAbandon?: () => void
}) {
  const [text, setText] = useState('')
  const settledRef = useRef(false)
  const abandonTimer = useRef<number | null>(null)
  useEffect(() => {
    if (abandonTimer.current !== null) {
      window.clearTimeout(abandonTimer.current)
      abandonTimer.current = null
    }
    return () => {
      abandonTimer.current = window.setTimeout(() => {
        if (!settledRef.current) onAbandon?.()
      }, 0)
    }
  }, [])
  const settle = (ok: boolean) => {
    if (settledRef.current) return
    settledRef.current = true
    onResult(ok)
  }
  const gateOk = !typed || text.trim() === opts.expected
  return (
    <Modal
      title={opts.heading}
      onClose={() => settle(false)}
      width={480}
      footer={
        <>
          <Button onClick={() => settle(false)}>Cancel</Button>
          <ActionButton
            primary={!opts.destructive}
            danger={opts.destructive}
            disabled={!gateOk}
            onClick={() => settle(true)}
          >
            {opts.confirmLabel ?? 'Confirm'}
          </ActionButton>
        </>
      }
    >
      {opts.body?.split('\n').map((line, i) => (
        <p key={i} className="confirm-body">
          {line}
        </p>
      ))}
      {typed && (
        <label className="field">
          <span className="field-label">
            Type <code>{opts.expected}</code> to confirm:
          </span>
          <TextInput
            autoFocus
            value={text}
            placeholder={opts.expected}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && gateOk && settle(true)}
          />
        </label>
      )}
    </Modal>
  )
}

/** Promise-based light confirm — pushes above whatever dialog is open. */
export function useConfirm() {
  const { pushDialog, popDialog } = useApp()
  return useCallback(
    (opts: ConfirmOpts) =>
      new Promise<boolean>((resolve) => {
        pushDialog(
          <ConfirmView
            opts={opts}
            onResult={(ok) => {
              popDialog()
              resolve(ok)
            }}
            onAbandon={() => resolve(false)}
          />,
        )
      }),
    [pushDialog, popDialog],
  )
}

/** Promise-based type-to-confirm (destructive tier). */
export function useTypedConfirm() {
  const { pushDialog, popDialog } = useApp()
  return useCallback(
    (opts: TypedConfirmOpts) =>
      new Promise<boolean>((resolve) => {
        pushDialog(
          <ConfirmView
            typed
            opts={opts}
            onResult={(ok) => {
              popDialog()
              resolve(ok)
            }}
            onAbandon={() => resolve(false)}
          />,
        )
      }),
    [pushDialog, popDialog],
  )
}

// -------------------------------------------------------- confirm + checkbox

export interface ConfirmBackupOpts extends ConfirmOpts {
  /** Default-on checkbox text (v2 §21 backup-before-update). */
  checkboxLabel?: string
}

function BackupConfirmView({
  opts,
  onResult,
  onAbandon,
}: {
  opts: ConfirmBackupOpts
  onResult: (r: { ok: boolean; checked: boolean }) => void
  /** 3.1.0 B14 — see ConfirmView.onAbandon. */
  onAbandon?: () => void
}) {
  const [checked, setChecked] = useState(true)
  const settledRef = useRef(false)
  const abandonTimer = useRef<number | null>(null)
  useEffect(() => {
    if (abandonTimer.current !== null) {
      window.clearTimeout(abandonTimer.current)
      abandonTimer.current = null
    }
    return () => {
      abandonTimer.current = window.setTimeout(() => {
        if (!settledRef.current) onAbandon?.()
      }, 0)
    }
  }, [])
  const settle = (r: { ok: boolean; checked: boolean }) => {
    if (settledRef.current) return
    settledRef.current = true
    onResult(r)
  }
  return (
    <Modal
      title={opts.heading}
      onClose={() => settle({ ok: false, checked })}
      width={520}
      footer={
        <>
          <Button onClick={() => settle({ ok: false, checked })}>Cancel</Button>
          <ActionButton
            primary={!opts.destructive}
            danger={opts.destructive}
            onClick={() => settle({ ok: true, checked })}
          >
            {opts.confirmLabel ?? 'Confirm'}
          </ActionButton>
        </>
      }
    >
      {opts.body?.split('\n').map((line, i) => (
        <p key={i} className="confirm-body">
          {line}
        </p>
      ))}
      <label className="checkbox inline">
        <input type="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} />
        <span />
        {opts.checkboxLabel ?? 'Back up database first'}
      </label>
    </Modal>
  )
}

/** Promise-based confirm with a default-on backup checkbox (v2 §21). */
export function useConfirmBackup() {
  const { pushDialog, popDialog } = useApp()
  return useCallback(
    (opts: ConfirmBackupOpts) =>
      new Promise<{ ok: boolean; checked: boolean }>((resolve) => {
        pushDialog(
          <BackupConfirmView
            opts={opts}
            onResult={(r) => {
              popDialog()
              resolve(r)
            }}
            onAbandon={() => resolve({ ok: false, checked: true })}
          />,
        )
      }),
    [pushDialog, popDialog],
  )
}

// ----------------------------------------------------------------- progress

interface ProgressState {
  title: string
  lines: string[]
  done: boolean
}

/**
 * Streaming progress for cancellable ops. Subscribes to progress-line /
 * progress-done (with replay) keyed by op_id; cancel calls the facade.
 */
export function ProgressDialog({
  opId,
  title,
  onClose,
}: {
  opId: string
  title: string
  onClose: () => void
}) {
  const [state, setState] = useState<ProgressState>({ title, lines: [], done: false })
  const [cancelMsg, setCancelMsg] = useState('')

  useEffect(() => {
    const offLine = onEvent(
      'progress-line',
      (p) => {
        if (p.op_id !== opId) return
        setState((prev) => ({ ...prev, lines: [...prev.lines, p.line].slice(-200) }))
      },
      true,
    )
    const offDone = onEvent(
      'progress-done',
      (p) => {
        if (p.op_id !== opId) return
        setState((prev) => ({ ...prev, done: true }))
      },
      true,
    )
    return () => {
      offLine()
      offDone()
    }
  }, [opId])

  const cancel = async () => {
    const api = getApi()
    // 3.2.0 P1: a rejected cancel (both registries missed the op) used to
    // escape as an unhandled rejection with no feedback.
    try {
      const res = await api.modules.cancel(opId)
      const final = res.ok ? res : await api.wizards.cancel(opId)
      setCancelMsg(final.message)
    } catch (err) {
      setCancelMsg(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <Modal
      title={state.title}
      width={560}
      onClose={state.done ? onClose : undefined}
      footer={
        state.done ? (
          <ActionButton primary onClick={onClose}>
            Close
          </ActionButton>
        ) : (
          <>
            <span className="dim-label">{cancelMsg || 'Working…'}</span>
            <Button danger onClick={() => void cancel()}>
              Cancel
            </Button>
          </>
        )
      }
    >
      <LineList lines={state.lines} mono maxHeight={320} />
      {!state.done && state.lines.length === 0 && (
        <p className="dim-label">Starting…</p>
      )}
    </Modal>
  )
}

/**
 * Run a cancellable op under a progress dialog.
 * `fn` receives the op_id and should pass it to the facade method.
 */
export function useProgressRun() {
  const { pushDialog, popDialog } = useApp()
  const seq = useRef(0)
  return useCallback(
    async <T extends { ok: boolean; message: string }>(
      title: string,
      fn: (opId: string) => Promise<T>,
    ): Promise<T | { ok: false; message: string }> => {
      seq.current += 1
      const opId = `op-${Date.now()}-${seq.current}`
      pushDialog(
        <ProgressDialog
          opId={opId}
          title={title}
          onClose={() => {
            popDialog()
          }}
        />,
      )
      try {
        return await fn(opId)
      } catch (err) {
        return { ok: false, message: err instanceof Error ? err.message : String(err) }
      }
    },
    [pushDialog, popDialog],
  )
}

