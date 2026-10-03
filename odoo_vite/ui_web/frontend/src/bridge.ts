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
import type { Answer, ApiTree, AuditEvent, Dict, InstanceRow, Result } from './types'

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
    theme: async (): Promise<string> => 'system',
    save_theme: async (theme: string): Promise<Result> => ({
      ok: true,
      message: `mock: theme saved (${theme})`,
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
    start_many: async (ids: string[]): Promise<Result> => ({
      ok: true,
      message: `mock: started ${ids.length} instance(s)`,
    }),
    stop: async (id: string): Promise<Result> => {
      const res: Result = { ok: true, message: `mock: stop ${id}` }
      route({ kind: 'message', payload: { text: res.message, level: 'info' } })
      route({ kind: 'refresh', payload: {} })
      return res
    },
    stop_many: async (ids: string[]): Promise<Result> => ({
      ok: true,
      message: `mock: stopped ${ids.length} instance(s)`,
    }),
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
  marketplace: {
    search: async (): Promise<Result> => ({
      ok: true,
      message: 'mock',
      data: {
        items: [
          {
            id: '17.0/demo_app',
            series: '17.0',
            tech: 'demo_app',
            title: 'Demo App (mock)',
            summary: 'A marketplace item for browser dev.',
            author: 'Mock Publisher',
            price: 'FREE',
            free: true,
            rating_count: 4,
            rating_stars: 4,
            purchases: 12,
            cover_url: '',
            source: 'mirror',
            official: false,
            featured: false,
          },
        ],
        total: 1,
        page: 1,
        offline: false,
        note: 'mock',
      },
    }),
    detail: async (): Promise<Result> => ({
      ok: true,
      message: 'mock',
      data: {
        id: '17.0/demo_app',
        series: '17.0',
        tech: 'demo_app',
        title: 'Demo App (mock)',
        summary: 'A marketplace item for browser dev.',
        author: 'Mock Publisher',
        price: 'FREE',
        free: true,
        rating_count: 4,
        rating_value: 4,
        review_count: 0,
        purchases: 12,
        downloads: 34,
        cover_url: '',
        source: 'mirror',
        official: false,
        featured: false,
        license: 'LGPL-3',
        depends: [{ label: 'Base', tech: 'base' }],
        versions: ['17.0'],
        screenshots: [],
        description_html: '<p>Mock description — browser dev.</p>',
        dl_hash: '',
        dl_version: '17.0',
        available: { odoo_online: true, odoosh: true, on_premise: true },
        reviews: [],
        local_reviews: [],
      },
    }),
    stats: async (): Promise<Result> => ({
      ok: true,
      message: 'mock',
      data: {
        site_total: 93421,
        cached: 1,
        github: 0,
        installs: 0,
        reviews: 0,
        avg_local_rating: 0,
        categories: ['Tools', 'Sales'],
        featured: 0,
        official: 0,
        base_url: 'mock',
      },
    }),
    categories: async (): Promise<Result> => ({
      ok: true,
      message: 'mock',
      data: ['Tools', 'Sales'],
    }),
    index: async (): Promise<Result> => ({
      ok: false,
      message: 'mock: no network in browser dev',
    }),
    add_review: async (): Promise<Result> => ({
      ok: true,
      message: 'mock: review saved',
    }),
    delete_review: async (): Promise<Result> => ({
      ok: true,
      message: 'mock: review deleted',
    }),
    sync_featured: async (): Promise<Result> => ({
      ok: false,
      message: 'mock: no network in browser dev',
    }),
    install_from_zip: async (): Promise<Result> => ({
      ok: false,
      message: 'mock: no zip import in browser dev',
    }),
    watch_download: async (): Promise<Result> => ({
      ok: false,
      message: 'mock: no download watch in browser dev',
    }),
    open_url: async (): Promise<Answer> => ({
      ok: false,
      message: 'mock: no browser in browser dev',
    }),
    installed_in: async (): Promise<Dict[]> => [],
    record_download: async (): Promise<Answer> => ({ ok: true, message: 'counted' }),
    cancel: async (): Promise<Answer> => ({ ok: false, message: 'mock: nothing running' }),
  } as unknown as ApiTree['marketplace'],
  config: {
    venv_status: async () => ({ venv_path: '/mock/venv', python_ok: true }),
    rebuild_venv: async (): Promise<Result> => ({
      ok: false,
      message: 'mock: no venv rebuild in browser dev',
    }),
  } as unknown as ApiTree['config'],
  logs: {
    // initial:true every tick = idempotent replace (no mock append path)
    tail: async (): Promise<Answer & { lines: string[]; initial?: boolean; rotated?: boolean }> => ({
      ok: true,
      message: 'ok',
      lines: ['mock log line — browser dev', 'another mock line'],
      initial: true,
    }),
  } as unknown as ApiTree['logs'],
  devtools: {} as ApiTree['devtools'],
  wizards: {
    install_requirements: async (): Promise<Result> => ({
      ok: false,
      message: 'mock: no apt install in browser dev',
    }),
  } as unknown as ApiTree['wizards'],
  transfer: {} as ApiTree['transfer'],
  audit: {
    tail: async (): Promise<AuditEvent[]> => [
      {
        ts: new Date().toISOString(),
        instance_id: 'mock-demo',
        instance_name: 'Demo (mock)',
        action: 'start',
        detail: 'mock event — browser dev',
      },
    ],
  },
  watch: {
    start: async (): Promise<Result> => ({
      ok: false,
      message: 'mock: no file watcher in browser dev',
    }),
    stop: async (): Promise<Result> => ({ ok: false, message: 'mock: not watching' }),
    status: async () => ({ watching: false, roots: [], changes: 0, fires: 0 }),
  },
}

/** The API to call from the shell: native bridge when present, mock otherwise. */
export function getApi(): ApiTree {
  return window.pywebview?.api ?? mock
}

/** Simulate a push from mock code (used by browser-dev demo controls). */
export function simulate<K extends keyof Dict>(kind: K, payload: Dict): void {
  route({ kind, payload } as never)
}
