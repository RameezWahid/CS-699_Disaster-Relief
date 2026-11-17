import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { api, type Alert, type Me } from './api.ts'

// Manual "official alert active" switch (spec 4.10): IMD warnings are too fragile to scrape, so a
// coordinator records them. An active alert raises credibility for incidents in that ward.
export default function AlertsPanel({ me }: { me: Me }) {
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [title, setTitle] = useState('IMD red alert')
  const [ward, setWard] = useState(me.wards[0] ?? '')
  const [hours, setHours] = useState(6)
  const [message, setMessage] = useState('')

  const load = useCallback(() => {
    api<Alert[]>('/api/alerts').then(setAlerts, (e) => setMessage(e.message))
  }, [])
  useEffect(load, [load])

  async function raise(event: FormEvent) {
    event.preventDefault()
    const start = new Date()
    try {
      await api('/api/alerts', {
        body: { title, ward: ward || null, starts_at: start.toISOString(), ends_at: new Date(start.getTime() + hours * 3600_000).toISOString() },
      })
      setMessage('Alert raised')
      load()
    } catch (e) {
      setMessage(e instanceof Error ? e.message : 'could not raise alert')
    }
  }

  async function end(id: number) {
    await api(`/api/alerts/${id}/end`, { method: 'POST' }).then(load, (e) => setMessage(e.message))
  }

  return (
    <>
      {alerts.length ? (
        <ul className="plain">
          {alerts.map((a) => (
            <li key={a.id}>
              <strong>{a.title}</strong> · {a.ward ? `ward ${a.ward}` : 'city-wide'} · until {new Date(a.ends_at).toLocaleTimeString()}
              {me.role !== 'responder' && <button className="link" onClick={() => end(a.id)}>End</button>}
            </li>
          ))}
        </ul>
      ) : <p className="hint">No active official alerts.</p>}
      {me.role !== 'responder' && (
        <form className="offer-form" onSubmit={raise}>
          <h3>Record an official alert</h3>
          <label>Title<input value={title} onChange={(e) => setTitle(e.target.value)} required minLength={3} /></label>
          <label>Ward<select value={ward} onChange={(e) => setWard(e.target.value)}>
            {me.wards.map((w) => <option key={w} value={w}>{w}</option>)}
            {me.role === 'admin' && <option value="">city-wide</option>}
          </select></label>
          <label>Hours<input type="number" min={1} max={72} value={hours} onChange={(e) => setHours(Number(e.target.value))} /></label>
          <button type="submit" className="primary">Raise alert</button>
          {message && <p className="status" role="status">{message}</p>}
        </form>
      )}
    </>
  )
}
