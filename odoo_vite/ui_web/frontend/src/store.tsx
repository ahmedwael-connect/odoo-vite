/**
 * App-wide React state: instance list + statuses (2s poll), current
 * selection, toast (fed by `message` events), dialog host, busy spinner.
 *
 * Mirrors the Slint bridge's refresh()/select()/show_toast() trio.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { getApi } from './bridge'
import { onEvent } from './events'
import type { InstanceRow, StatusRow } from './types'

export interface Toast {
  id: number
  text: string
  level: string
  /** wall-clock ms after which the toast auto-dismisses */
  until: number
}

const TOAST_MS = 5000
const TOAST_MAX = 3

interface AppStore {
  statuses: StatusRow[]
  instances: InstanceRow[]
  currentId: string | null
  current: InstanceRow | null
  select: (id: string) => void
  refresh: () => void
  toasts: Toast[]
  hideToast: (id?: number) => void
  busy: boolean
  setBusy: (busy: boolean) => void
  /** modal stack — rendered in order, last entry is topmost */
  dialogs: ReactNode[]
  /** replace the whole stack with one dialog (or clear with null) */
  setDialog: (node: ReactNode | null) => void
  /** push above the current stack (nested confirms / progress) */
  pushDialog: (node: ReactNode) => void
  /** pop the topmost dialog */
  popDialog: () => void
  error: string
}

const Ctx = createContext<AppStore | null>(null)

export function useApp(): AppStore {
  const store = useContext(Ctx)
  if (!store) throw new Error('useApp outside AppProvider')
  return store
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [statuses, setStatuses] = useState<StatusRow[]>([])
  const [instances, setInstances] = useState<InstanceRow[]>([])
  const [currentId, setCurrentId] = useState<string | null>(null)
  const [toasts, setToasts] = useState<Toast[]>([])
  const [busy, setBusy] = useState(false)
  const [dialogs, setDialogs] = useState<ReactNode[]>([])
  const [error, setError] = useState('')
  const toastSeq = useRef(0)

  const loadStatuses = useCallback(async () => {
    try {
      setStatuses(await getApi().app.statuses())
      setError('')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }, [])

  const loadInstances = useCallback(async () => {
    try {
      const list = await getApi().app.instances()
      setInstances(list)
      // auto-select on boot: with currentId null every view button is
      // silently disabled (the "pressed Search, nothing happened" footgun);
      // also recover if the selected instance was removed
      setCurrentId((prev) =>
        prev && list.some((i) => i.id === prev) ? prev : (list[0]?.id ?? null),
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }, [])

  const refresh = useCallback(() => {
    void loadStatuses()
    void loadInstances()
  }, [loadStatuses, loadInstances])

  // boot + 2s status poll (Slint bridge's poll timer)
  useEffect(() => {
    refresh()
    const timer = window.setInterval(loadStatuses, 2000)
    const offRefresh = onEvent('refresh', () => refresh())
    const offMessage = onEvent('message', (payload) => {
      toastSeq.current += 1
      const text = String(payload.text)
      const level = String(payload.level ?? 'info')
      setToasts((prev) => {
        const until = Date.now() + TOAST_MS
        // dedupe: an identical visible toast refreshes instead of stacking
        const filtered = prev.filter((t) => !(t.text === text && t.level === level))
        return [...filtered, { id: toastSeq.current, text, level, until }].slice(-TOAST_MAX)
      })
    })
    return () => {
      window.clearInterval(timer)
      offRefresh()
      offMessage()
    }
  }, [refresh, loadStatuses])

  // per-toast auto-dismiss (each keeps its own deadline across pushes)
  useEffect(() => {
    if (!toasts.length) return
    const now = Date.now()
    const timers = toasts.map((t) =>
      window.setTimeout(
        () => setToasts((prev) => prev.filter((x) => x.id !== t.id)),
        Math.max(0, t.until - now),
      ),
    )
    return () => timers.forEach((timer) => window.clearTimeout(timer))
  }, [toasts])

  const select = useCallback(
    (id: string) => {
      setCurrentId(id)
    },
    [],
  )

  const hideToast = useCallback((id?: number) => {
    setToasts((prev) => (id === undefined ? [] : prev.filter((t) => t.id !== id)))
  }, [])

  const setDialog = useCallback((node: ReactNode | null) => {
    setDialogs(node ? [node] : [])
  }, [])
  const pushDialog = useCallback((node: ReactNode) => {
    setDialogs((prev) => [...prev, node])
  }, [])
  const popDialog = useCallback(() => {
    setDialogs((prev) => prev.slice(0, -1))
  }, [])

  const current = instances.find((i) => i.id === currentId) ?? null

  const store: AppStore = {
    statuses,
    instances,
    currentId,
    current,
    select,
    refresh,
    toasts,
    hideToast,
    busy,
    setBusy,
    dialogs,
    setDialog,
    pushDialog,
    popDialog,
    error,
  }

  return <Ctx.Provider value={store}>{children}</Ctx.Provider>
}
