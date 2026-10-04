/**
 * Top-level error boundary (3.2.0 P0): a render crash anywhere under it
 * swaps in a static fallback instead of leaving a white window — pywebview
 * has no devtools console to recover from.
 */

import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('Unhandled render error:', error, info.componentStack)
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children
    return (
      <div className="view" role="alert">
        <div className="banner banner-error">
          <span className="banner-text">The interface hit an unexpected error.</span>
        </div>
        <p className="dim-label mono">{error.message || String(error)}</p>
        <div className="btn-row">
          <button type="button" className="btn primary" onClick={() => window.location.reload()}>
            Reload app
          </button>
        </div>
      </div>
    )
  }
}
