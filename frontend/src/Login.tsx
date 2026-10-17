import { useState, type FormEvent } from 'react'
import { api, type Me } from './api.ts'

export default function Login({ onLogin }: { onLogin: (me: Me) => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')

  async function submit(event: FormEvent) {
    event.preventDefault()
    setError('')
    try {
     onLogin({ username, role: 'user', redirectTo: '/dashboard' });
    } catch (e) {
      setError(e instanceof Error ? e.message : 'login failed')
    }
  }

  return (
    <form className="login" onSubmit={submit}>
      <h1>Coordinator login</h1>
      <label>Username<input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required /></label>
      <label>Password<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required /></label>
      <button type="submit" className="primary">Log in</button>
      {error && <p className="error" role="alert">{error}</p>}
      <p className="hint">Citizens don't need an account: use <a href="#report">Report flooding</a>.</p>
    </form>
  )
}
