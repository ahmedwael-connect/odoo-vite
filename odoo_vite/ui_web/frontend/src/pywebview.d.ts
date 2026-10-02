import type { ApiTree, Envelope } from './types'

declare global {
  interface Window {
    /** Injected by pywebview once the bridge is ready. */
    pywebview?: { api: ApiTree }
    /** Push target: Python calls `window.odooVite.push(envelope)`. */
    odooVite?: { push(envelope: Envelope | string): void }
  }
}

export {}
