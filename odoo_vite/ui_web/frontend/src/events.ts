/**
 * Python -> JS push router.
 *
 * Python (ui_web/push.py) does:
 *   window.odooVite && window.odooVite.push({"kind": ..., "payload": ...})
 * which lands here. Subscriptions are React-friendly: one Set per kind.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import type { Envelope, EventKind, EventPayloads } from './types'

type Listener<K extends EventKind> = (payload: EventPayloads[K]) => void

const listeners = new Map<EventKind, Set<Listener<EventKind>>>()

/**
 * Ring buffer of recent envelopes. Subscribers that mount later (a tab was
 * not open when the event fired) replay the buffered events of their kind —
 * otherwise messages/progress emitted before mount would be lost forever.
 */
const buffer: Envelope[] = []
const BUFFER_CAP = 2000

function dispatch<K extends EventKind>(kind: K, payload: EventPayloads[K]) {
  const set = listeners.get(kind)
  if (!set) return
  for (const listener of [...set]) {
    try {
      ;(listener as Listener<K>)(payload)
    } catch (err) {
      // A broken subscriber must never take down the push channel.
      console.error(`[events] listener for ${kind} threw`, err)
    }
  }
}

/** Entry point for `window.odooVite.push`. Tolerates pre-stringified payloads. */
export function route(envelope: Envelope | string) {
  let env: Envelope
  try {
    env = typeof envelope === 'string' ? JSON.parse(envelope) : envelope
  } catch (err) {
    console.error('[events] unparseable envelope', err)
    return
  }
  if (!env || typeof env.kind !== 'string') return
  const payload = (env.payload ?? {}) as EventPayloads[EventKind]
  buffer.push({ kind: env.kind, payload } as Envelope)
  if (buffer.length > BUFFER_CAP) buffer.splice(0, buffer.length - BUFFER_CAP)
  dispatch(env.kind, payload)
}

export function onEvent<K extends EventKind>(
  kind: K,
  listener: Listener<K>,
  replay = false,
): () => void {
  let set = listeners.get(kind)
  if (!set) {
    set = new Set()
    listeners.set(kind, set)
  }
  set.add(listener as Listener<EventKind>)
  if (replay) {
    for (const env of buffer) {
      if (env.kind === kind) {
        try {
          ;(listener as Listener<K>)(env.payload as EventPayloads[K])
        } catch (err) {
          console.error(`[events] replay listener for ${kind} threw`, err)
        }
      }
    }
  }
  return () => {
    set!.delete(listener as Listener<EventKind>)
  }
}

/** 3.1.0 B16: fire-and-forget facade calls (`void api.…`) used to reject
 * with no handler — the classic "clicked, nothing happened". Every
 * unhandled rejection now surfaces as an error toast; call sites that
 * already have a `.catch()` preempt it. Console logging stays on. */
function onUnhandledRejection(ev: PromiseRejectionEvent) {
  const reason: unknown = ev.reason
  const text =
    reason instanceof Error
      ? reason.message
      : typeof reason === 'string'
        ? reason
        : reason
          ? JSON.stringify(reason)
          : 'unknown error'
  route({ kind: 'message', payload: { text: `Operation failed: ${text}`, level: 'error' } })
}

/** Install the push target. Returns a cleanup that restores nothing (idempotent). */
export function installPushTarget(): () => void {
  window.odooVite = { push: route }
  window.addEventListener('unhandledrejection', onUnhandledRejection)
  return () => {
    window.removeEventListener('unhandledrejection', onUnhandledRejection)
    delete window.odooVite
  }
}

/** Subscribe a component to one event kind; handler always sees the latest closure. */
export function useEvent<K extends EventKind>(
  kind: K,
  handler: (payload: EventPayloads[K]) => void,
  replay = false,
) {
  const handlerRef = useRef(handler)
  handlerRef.current = handler
  useEffect(() => onEvent(kind, (payload) => handlerRef.current(payload), replay), [kind, replay])
}

/** Rolling window of `message` events for the log pane (replays history). */
export interface LogLine {
  text: string
  level: string
  at: number
}

export function useMessageLog(limit = 200): LogLine[] {
  const [lines, setLines] = useState<LogLine[]>([])
  useEvent(
    'message',
    (payload) => setLines((prev) => [...prev, { ...payload, at: Date.now() }].slice(-limit)),
    true,
  )
  return lines
}

/** Auto-refresh helper: calls `fn` on mount and on every `refresh` event. */
export function useRefreshEvent(fn: () => void) {
  const fnRef = useRef(fn)
  fnRef.current = fn
  useEvent('refresh', useCallback(() => fnRef.current(), []))
}

/** Long-running operations, fed by progress-line/progress-done (3.1.0 C1:
 * startedAt/doneAt feed the live operations dialog). */
export interface ProgressOp {
  op_id: string
  lines: string[]
  done: boolean
  startedAt: number
  doneAt?: number
}

const FINISHED_OP_CAP = 40

function pruneFinished(ops: Record<string, ProgressOp>): Record<string, ProgressOp> {
  const finished = Object.values(ops)
    .filter((o) => o.done)
    .sort((a, b) => (a.doneAt ?? a.startedAt) - (b.doneAt ?? b.startedAt))
  const excess = finished.length - FINISHED_OP_CAP
  if (excess <= 0) return ops
  const drop = new Set(finished.slice(0, excess).map((o) => o.op_id))
  const next: Record<string, ProgressOp> = {}
  for (const [k, v] of Object.entries(ops)) if (!drop.has(k)) next[k] = v
  return next
}

export function useProgress(): ProgressOp[] {
  const [ops, setOps] = useState<Record<string, ProgressOp>>({})
  useEvent(
    'progress-line',
    (payload) => {
      if (!payload.op_id) return
      setOps((prev) => {
        const op = prev[payload.op_id] ?? {
          op_id: payload.op_id,
          lines: [],
          done: false,
          startedAt: Date.now(),
        }
        return {
          ...prev,
          [payload.op_id]: {
            ...op,
            done: false,
            doneAt: undefined,
            lines: [...op.lines, payload.line].slice(-50),
          },
        }
      })
    },
    true,
  )
  useEvent(
    'progress-done',
    (payload) => {
      if (!payload.op_id) return
      setOps((prev) => {
        const op = prev[payload.op_id]
        const next = op
          ? { ...op, done: true, doneAt: Date.now() }
          : {
              op_id: payload.op_id,
              lines: [],
              done: true,
              startedAt: Date.now(),
              doneAt: Date.now(),
            }
        return pruneFinished({ ...prev, [payload.op_id]: next })
      })
    },
    true,
  )
  return Object.values(ops)
}
