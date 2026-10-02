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

export interface ModalProps {
  title: string
  children?: ReactNode
  footer?: ReactNode
  onClose?: () => void
  width?: number
}

export function Modal({ title, children, footer, onClose, width }: ModalProps) {
  return (
    <div className="modal-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose?.()}>
      <div className="modal" style={width ? { width } : undefined} role="dialog" aria-label={title}>
        <header className="modal-head">
          <h2 className="title">{title}</h2>
          {onClose && (
            <button type="button" className="modal-x" onClick={onClose} aria-label="Close">
              ×
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
}: {
  opts: ConfirmOpts
  typed?: boolean
  onResult: (ok: boolean) => void
}) {
  const [text, setText] = useState('')
  const gateOk = !typed || text.trim() === opts.expected
  return (
    <Modal
      title={opts.heading}
      onClose={() => onResult(false)}
      width={480}
      footer={
        <>
          <Button onClick={() => onResult(false)}>Cancel</Button>
          <ActionButton
            primary={!opts.destructive}
            danger={opts.destructive}
            disabled={!gateOk}
            onClick={() => onResult(true)}
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
            onKeyDown={(e) => e.key === 'Enter' && gateOk && onResult(true)}
          />
        </label>
      )}
    </Modal>
  )
}

/** Promise-based light confirm via the store's dialog host. */
export function useConfirm() {
  const { setDialog } = useApp()
  return useCallback(
    (opts: ConfirmOpts) =>
      new Promise<boolean>((resolve) => {
        setDialog(
          <ConfirmView opts={opts} onResult={(ok) => { setDialog(null); resolve(ok) }} />,
        )
      }),
    [setDialog],
  )
}

/** Promise-based type-to-confirm (destructive tier). */
export function useTypedConfirm() {
  const { setDialog } = useApp()
  return useCallback(
    (opts: TypedConfirmOpts) =>
      new Promise<boolean>((resolve) => {
        setDialog(
          <ConfirmView
            opts={opts}
            typed
            onResult={(ok) => { setDialog(null); resolve(ok) }}
          />,
        )
      }),
    [setDialog],
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
    const res = await api.modules.cancel(opId)
    const final = res.ok ? res : await api.wizards.cancel(opId)
    setCancelMsg(final.message)
  }

  return (
    <Modal
      title={state.title}
      width={560}
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
  const { setDialog } = useApp()
  const seq = useRef(0)
  return useCallback(
    async (title: string, fn: (opId: string) => Promise<{ ok: boolean; message: string }>) => {
      seq.current += 1
      const opId = `op-${Date.now()}-${seq.current}`
      setDialog(
        <ProgressDialog
          opId={opId}
          title={title}
          onClose={() => {
            setDialog(null)
          }}
        />,
      )
      try {
        return await fn(opId)
      } catch (err) {
        return { ok: false, message: err instanceof Error ? err.message : String(err) }
      }
    },
    [setDialog],
  )
}
