import { useEffect, useState } from 'react'
import { api, type Me } from './api.ts'
import Dashboard from './Dashboard.tsx'
import Login from './Login.tsx'
import ReportForm from './ReportForm.tsx'

type View = 'dashboard' | 'report'

function currentView(): View {
  return window.location.hash === '#report' ? 'report' : 'dashboard'
}

export default function App() {
  const [view, setView] = useState<View>(currentView)
  // undefined: still checking the session; null: logged out.
  const [me, setMe] = useState<Me | null | undefined>(undefined)

  useEffect(() => {
    const onHashChange = () => setView(currentView())
    window.addEventListener('hashchange', onHashChange)
    api<Me>('/api/auth/me').then(setMe, () => setMe(null))
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  async function logout() {
    await api('/api/auth/logout', { method: 'POST' })
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
      </nav>
      <main>
        {view === 'report' && <ReportForm />}
        {view === 'dashboard' && me === null && <Login onLogin={setMe} />}
        {view === 'dashboard' && me && <Dashboard me={me} onLogout={logout} />}
      </main>
    </div>
  )
}
