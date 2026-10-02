/**
 * Access to the Python facade (`odoo_vite.ui_web.api.Api`).
 *
 * Native (inside the pywebview window): `window.pywebview.api` mirrors the
 * Api tree; every call runs a Python worker thread and resolves a promise.
 *
 * Browser dev (`npm run dev` without the window): a tiny mock answers the
 * handful of calls the shell makes, and can simulate push events so the
 * event wiring stays testable without Python.
 */

import { useEffect, useState } from 'react'
import { route } from './events'
import type { Answer, ApiTree, Dict, InstanceRow, Result } from './types'

export function isNative(): boolean {
  return typeof window.pywebview?.api === 'object'
}

/** Resolves true once the pywebview bridge is up (or immediately in browser dev). */
export function useBridgeReady(): { ready: boolean; native: boolean } {
  const [ready, setReady] = useState(isNative)
  useEffect(() => {
    if (isNative()) return
    const onReady = () => setReady(true)
    window.addEventListener('pywebviewready', onReady)
    const timer = window.setTimeout(() => setReady(isNative()), 6000)
    return () => {
      window.removeEventListener('pywebviewready', onReady)
      window.clearTimeout(timer)
    }
  }, [])
  return { ready, native: isNative() }
}

// ---------------------------------------------------------------- mock (dev)

const MOCK_INSTANCES: InstanceRow[] = [
  {
    id: 'mock-demo',
    name: 'Demo (mock)',
    version: '18.0',
    mode: 'managed',
    status: 'ready',
    port: 8069,
    path: '/tmp/odoo-vite/mock/demo',
    primary_db: 'demo',
    tracked_dbs: ['demo'],
    pid: null,
    last_error: null,
  },
]

const mock: ApiTree = {
  app: {
    instances: async () => MOCK_INSTANCES,
    statuses: async () => [],
    suggest_port: async (start = 8069) => start,
    preferences: async () => ({ mode: 'developer' }),
    save_preferences: async (mode: string): Promise<Result> => ({
      ok: true,
      message: `mock: preferences saved (${mode})`,
    }),
    pick_file: async (): Promise<Answer & { path?: string }> => ({
      ok: false,
      message: 'mock: no file dialog in browser dev',
    }),
    pick_dir: async (): Promise<Answer & { path?: string }> => ({
      ok: false,
      message: 'mock: no file dialog in browser dev',
    }),
    version: async () => 'dev (mock)',
    instance: async (id: string) => MOCK_INSTANCES.find((i) => i.id === id) ?? null,
    enterprise: async (): Promise<Result> => ({ ok: true, message: 'mock: no enterprise' }),
  },
  lifecycle: {
    start: async (id: string): Promise<Result> => {
      const res: Result = { ok: true, message: `mock: start ${id}` }
      route({ kind: 'message', payload: { text: res.message, level: 'info' } })
      route({ kind: 'refresh', payload: {} })
      return res
    },
    stop: async (id: string): Promise<Result> => {
      const res: Result = { ok: true, message: `mock: stop ${id}` }
      route({ kind: 'message', payload: { text: res.message, level: 'info' } })
      route({ kind: 'refresh', payload: {} })
      return res
    },
    restart: async (id: string): Promise<Result> => ({ ok: true, message: `mock: restart ${id}` }),
    remove: async (id: string): Promise<Result> => ({ ok: true, message: `mock: remove ${id}` }),
    clone: async (id: string, name: string): Promise<Result> => ({
      ok: true,
      message: `mock: clone ${id} -> ${name}`,
    }),
  },
  databases: {} as ApiTree['databases'],
  modules: {
    cancel: async (opId: string): Promise<Answer> =>
      opId.startsWith('mock') ? { ok: true, message: 'cancelling' } : { ok: false, message: `no running operation '${opId}'` },
  } as ApiTree['modules'],
  config: {} as ApiTree['config'],
  logs: {} as ApiTree['logs'],
  devtools: {} as ApiTree['devtools'],
  wizards: {} as ApiTree['wizards'],
  transfer: {} as ApiTree['transfer'],
}

/** The API to call from the shell: native bridge when present, mock otherwise. */
export function getApi(): ApiTree {
  return window.pywebview?.api ?? mock
}

/** Simulate a push from mock code (used by browser-dev demo controls). */
export function simulate<K extends keyof Dict>(kind: K, payload: Dict): void {
  route({ kind, payload } as never)
}
