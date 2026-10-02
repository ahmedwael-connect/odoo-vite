/**
 * Theme choice (dark / light / system) applied to <html data-theme="…">.
 *
 * Persistence has two layers, because browser storage under WebKitGTK is
 * ephemeral (no localStorage; sessionStorage/cookies die with the process):
 * 1. a fast session layer (localStorage → sessionStorage → cookie chain)
 * 2. the settings table via app.save_theme / app.theme — survives restarts;
 *    hydrated on boot when the session layer is empty
 */

import { getApi } from './bridge'

export type ThemeChoice = 'system' | 'dark' | 'light'

const KEY = 'odoo-vite-theme'

function storeGet(key: string): string | null {
  try {
    const v = window.localStorage.getItem(key)
    if (v !== null) return v
  } catch {
    /* localStorage unavailable (WebKitGTK) */
  }
  try {
    const v = window.sessionStorage.getItem(key)
    if (v !== null) return v
  } catch {
    /* sessionStorage unavailable */
  }
  const m = document.cookie.match(new RegExp(`(?:^|;\\s*)${key}=([^;]*)`))
  return m ? decodeURIComponent(m[1]) : null
}

function storeSet(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value)
    return
  } catch {
    /* localStorage unavailable (WebKitGTK) */
  }
  try {
    window.sessionStorage.setItem(key, value)
    return
  } catch {
    /* sessionStorage unavailable */
  }
  document.cookie = `${key}=${encodeURIComponent(value)};path=/`
}

export function getTheme(): ThemeChoice {
  const v = storeGet(KEY)
  return v === 'dark' || v === 'light' ? v : 'system'
}

export function resolveTheme(choice: ThemeChoice): 'dark' | 'light' {
  return choice === 'system' ? resolveSystem() : choice
}

export function applyTheme(choice: ThemeChoice): void {
  document.documentElement.dataset.theme = resolveTheme(choice)
}

function resolveSystem(): 'dark' | 'light' {
  try {
    return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
  } catch {
    return 'dark'
  }
}

export function setTheme(choice: ThemeChoice): void {
  storeSet(KEY, choice)
  applyTheme(choice)
  try {
    void getApi().app.save_theme(choice)
  } catch {
    /* bridge not ready — visual applies for this session */
  }
}

/**
 * Apply the saved choice now; while it is system/dark/light-less, follow
 * OS changes. Hydrates from the settings table on first boot.
 * Returns a cleanup for the caller's effect.
 */
export function initTheme(): () => void {
  const stored = storeGet(KEY)
  if (stored === 'dark' || stored === 'light') applyTheme(stored)
  else applyTheme('system')

  if (stored === null) {
    getApi()
      .app.theme()
      .then((t) => {
        if (storeGet(KEY) === null && (t === 'dark' || t === 'light' || t === 'system')) {
          if (t !== 'system') storeSet(KEY, t)
          applyTheme(t)
        }
      })
      .catch(() => undefined)
  }

  let mq: MediaQueryList
  try {
    mq = window.matchMedia('(prefers-color-scheme: light)')
  } catch {
    return () => undefined
  }
  const onChange = () => {
    const v = storeGet(KEY)
    if (v !== 'dark' && v !== 'light') applyTheme('system')
  }
  mq.addEventListener('change', onChange)
  return () => mq.removeEventListener('change', onChange)
}
