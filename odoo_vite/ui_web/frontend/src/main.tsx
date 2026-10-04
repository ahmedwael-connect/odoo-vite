import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.tsx'
import ErrorBoundary from './components/boundary.tsx'
import { installPushTarget } from './events'
import { AppProvider } from './store'
import './style.css'

// Install the Python push target before the first render: the facade may
// emit events as soon as the first API call resolves.
installPushTarget()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <AppProvider>
        <App />
      </AppProvider>
    </ErrorBoundary>
  </StrictMode>,
)
