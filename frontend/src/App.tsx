import { useEffect, useState } from 'react'
import { api, ApiError, type Me } from './api.ts'
import Dashboard from './Dashboard.tsx'
import Login from './Login.tsx'
import ReportForm from './ReportForm.tsx'
import Responder from './Responder.tsx'

type View = 'dashboard' | 'report' | 'responder'
const ME_KEY = 'me'

function currentView(): View {
  if (window.location.hash === '#report') return 'report'
  return window.location.hash === '#responder' ? 'responder' : 'dashboard'
}

// Offline, /api/auth/me cannot answer; the last known login lets the responder app open anyway.
function cachedMe(): Me | null {
  try {
    return JSON.parse(localStorage.getItem(ME_KEY) ?? 'null')
  } catch {
    return null
  }
}

function remember(me: Me | null) {
  if (me) localStorage.setItem(ME_KEY, JSON.stringify(me))
  else localStorage.removeItem(ME_KEY)
}

export default function App() {
  const [view, setView] = useState<View>(currentView)
  // undefined: still checking the session; null: logged out.
  const [me, setMe] = useState<Me | null | undefined>(undefined)

  useEffect(() => {
    const onHashChange = () => setView(currentView())
    window.addEventListener('hashchange', onHashChange)
    api<Me>('/api/auth/me').then(
      (user) => {
        remember(user)
        setMe(user)
      },
      (error) => setMe(error instanceof ApiError ? (remember(null), null) : cachedMe()),
    )
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  function login(user: Me) {
    remember(user)
    setMe(user)
  }

  async function logout() {
    await api('/api/auth/logout', { method: 'POST' })
    remember(null)
    setMe(null)
  }

  return (
    <div className="app">
      <div className="banner" role="alert">
        Research prototype, not monitored. In an emergency call <strong>112</strong>.
      </div>
      <nav className="tabs">
        <a href="#dashboard" aria-current={view === 'dashboard'}>Coordinator dashboard</a>
        <a href="#report" aria-current={view === 'report'}>Report flooding</a>
        <a href="#responder" aria-current={view === 'responder'}>Responder app</a>
      </nav>
      <main>
        {view === 'report' && <ReportForm />}
        {view !== 'report' && me === null && <Login onLogin={login} />}
        {view === 'responder' && me && <Responder me={me} />}
        {view === 'dashboard' && me && <Dashboard me={me} onLogout={logout} />}
      </main>
    </div>
  )
}
