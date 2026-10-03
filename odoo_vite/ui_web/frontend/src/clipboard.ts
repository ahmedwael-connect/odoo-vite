/**
 * Clipboard helper (3.1.0 N2). navigator.clipboard needs a secure context —
 * pywebview serves the app over http://127.0.0.1, so fall back to the
 * legacy execCommand path when it is unavailable or rejects.
 */

import { route } from './events'

export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    /* fall through to the legacy path */
  }
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.top = '-1000px'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.focus()
    ta.select()
    const ok = document.execCommand('copy')
    ta.remove()
    return ok
  } catch {
    return false
  }
}

/** Copy + toast ("Copied N lines" / failure). Returns success. */
export async function copyWithToast(text: string, what: string): Promise<boolean> {
  const ok = await copyText(text)
  route({
    kind: 'message',
    payload: {
      text: ok ? `Copied ${what}` : `Copy failed — clipboard unavailable`,
      level: ok ? 'info' : 'error',
    },
  })
  return ok
}

/** Download helper for exports (JSON/CSV) — frontend-only, no backend hop. */
export function downloadText(filename: string, mime: string, text: string) {
  const blob = new Blob([text], { type: mime })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}
