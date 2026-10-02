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
}

interface AppStore {
  statuses: StatusRow[]
  instances: InstanceRow[]
  currentId: string | null
  current: InstanceRow | null
  select: (id: string) => void
  refresh: () => void
  toast: Toast | null
  hideToast: () => void
  busy: boolean
  setBusy: (busy: boolean) => void
  dialog: ReactNode | null
  setDialog: (node: ReactNode | null) => void
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
  const [toast, setToast] = useState<Toast | null>(null)
  const [busy, setBusy] = useState(false)
  const [dialog, setDialog] = useState<ReactNode | null>(null)
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
      setInstances(await getApi().app.instances())
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
      setToast({ id: toastSeq.current, text: payload.text, level: payload.level })
    })
    return () => {
      window.clearInterval(timer)
      offRefresh()
      offMessage()
    }
  }, [refresh, loadStatuses])

  // auto-hide toasts after 5s (Slint toast timer)
  useEffect(() => {
    if (!toast) return
    const timer = window.setTimeout(() => setToast(null), 5000)
    return () => window.clearTimeout(timer)
  }, [toast])

  const select = useCallback(
    (id: string) => {
      setCurrentId(id)
    },
    [],
  )

  const hideToast = useCallback(() => setToast(null), [])

  const current = instances.find((i) => i.id === currentId) ?? null

  const store: AppStore = {
    statuses,
    instances,
    currentId,
    current,
    select,
    refresh,
    toast,
    hideToast,
    busy,
    setBusy,
    dialog,
    setDialog,
    error,
  }

  return <Ctx.Provider value={store}>{children}</Ctx.Provider>
}
