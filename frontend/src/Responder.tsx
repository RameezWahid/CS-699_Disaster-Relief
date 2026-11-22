import { useCallback, useEffect, useState, type FormEvent } from 'react'
import type { Me } from './api.ts'
import { clearRejected, enqueue, flush, pending, rejected, type FlushOutcome } from './outbox.ts'

// Offline-first responder app, demo scope (spec 4.7). Tasks are cached in localStorage, every
// action goes through the outbox, and the outbox is flushed on reconnect, on focus, and on load.

interface Task {
  id: number
  status: 'accepted' | 'dispatched' | 'delivered'
  qty: number
  kind: string
  provider: string
  incident_id: number
  category: string
  ward: string | null
  urgency_level: number
  headcount: number | null
  lon: number | null
  lat: number | null
}

const TASKS_KEY = 'responder_tasks'
const NEXT_STEP = { accepted: ['dispatch', 'Mark dispatched', 'dispatched'], dispatched: ['deliver', 'Mark delivered', 'delivered'] } as const
const REPORT_CATEGORIES: [string, string][] = [
  ['road_blocked', 'Road blocked'],
  ['rescue', 'People trapped'],
  ['medical', 'Medical'],
  ['food_water', 'Food / water'],
  ['shelter', 'Shelter'],
]

function cachedTasks(): Task[] {
  try {
    return JSON.parse(localStorage.getItem(TASKS_KEY) ?? '[]')
  } catch {
    return []
  }
}

function saveTasks(tasks: Task[]) {
  localStorage.setItem(TASKS_KEY, JSON.stringify(tasks))
}

// Fetch the whole basemap once so the service worker can serve map tiles with no network.
function keepBasemapOffline() {
  if (!('caches' in window)) return
  const url = '/basemap/mumbai.pmtiles'
  caches.open('basemap').then(async (cache) => {
    if (!(await cache.match(url))) await cache.add(url)
  }).catch(() => undefined)
}

export default function Responder({ me }: { me: Me }) {
  const [tasks, setTasks] = useState<Task[]>(cachedTasks)
  const [online, setOnline] = useState(navigator.onLine)
  const [waiting, setWaiting] = useState(pending().length)
  const [problems, setProblems] = useState(rejected())
  const [syncState, setSyncState] = useState<FlushOutcome | 'syncing'>('ok')
  const [text, setText] = useState('')
  const [category, setCategory] = useState('road_blocked')
  const [position, setPosition] = useState<{ lat: number; lon: number; accuracy: number } | null>(null)
  const [note, setNote] = useState('')

  const refreshCounts = () => {
    setWaiting(pending().length)
    setProblems(rejected())
  }

  const sync = useCallback(async () => {
    setSyncState('syncing')
    const outcome = await flush()
    refreshCounts()
    if (outcome === 'ok') {
      setNote((n) => (n.startsWith('Saved') ? 'Waiting reports sent' : n))
      try {
        const response = await fetch('/api/tasks')
        if (response.ok) {
          const fresh: Task[] = await response.json()
          saveTasks(fresh)
          setTasks(fresh)
        }
      } catch {
        // Still offline for reads; keep the cached tasks.
      }
    }
    setSyncState(outcome)
  }, [])

  useEffect(() => {
    keepBasemapOffline()
    sync()
    const goOnline = () => {
      setOnline(true)
      sync()
    }
    const goOffline = () => setOnline(false)
    const onFocus = () => document.visibilityState === 'visible' && sync()
    window.addEventListener('online', goOnline)
    window.addEventListener('offline', goOffline)
    document.addEventListener('visibilitychange', onFocus)
    return () => {
      window.removeEventListener('online', goOnline)
      window.removeEventListener('offline', goOffline)
      document.removeEventListener('visibilitychange', onFocus)
    }
  }, [sync])

  function advance(task: Task) {
    if (task.status === 'delivered') return
    const [action, , newStatus] = NEXT_STEP[task.status]
    enqueue({
      type: 'task_status',
      payload: { allocation_id: task.id, action },
      base_status: task.status,
      label: `${task.qty} × ${task.kind} for incident ${task.incident_id} (${newStatus})`,
    })
    // Show the change at once; the server's answer on sync replaces this copy.
    const updated = tasks.map((t) => (t.id === task.id ? { ...t, status: newStatus } : t))
    saveTasks(updated)
    setTasks(updated)
    refreshCounts()
    if (navigator.onLine) sync()
  }

  function locate() {
    setNote('Finding your location…')
    navigator.geolocation.getCurrentPosition(
      (p) => {
        setPosition({ lat: p.coords.latitude, lon: p.coords.longitude, accuracy: p.coords.accuracy })
        setNote('Location added')
      },
      (error) => setNote(`No location: ${error.message}`),
      { enableHighAccuracy: true, timeout: 15000 },
    )
  }

  function report(event: FormEvent) {
    event.preventDefault()
    if (!text.trim() && !position) return
    enqueue({
      type: 'report',
      payload: {
        text,
        category_hint: category,
        lat: position?.lat ?? null,
        lon: position?.lon ?? null,
        accuracy_m: position?.accuracy ?? null,
      },
      label: `Report: ${text.slice(0, 40) || category}`,
    })
    setText('')
    setPosition(null)
    setNote(navigator.onLine ? 'Report sent' : 'Saved; it will be sent when you are back online')
    refreshCounts()
    if (navigator.onLine) sync()
  }

  const statusLine =
    syncState === 'login' ? 'Session expired: log in again to send waiting actions'
      : syncState === 'syncing' ? 'Syncing…'
        : !online || syncState === 'offline' ? 'Offline: actions are saved on this phone'
          : 'Online and synced'

  return (
    <section className="responder">
      <header className="responder-head">
        <span className={`dot ${online && syncState !== 'offline' ? 'on' : 'off'}`} aria-hidden />
        <strong data-testid="sync-status">{statusLine}</strong>
        <span>{me.username} · wards {me.wards.join(', ') || 'all'}</span>
      </header>
      {waiting > 0 && (
        <p className="waiting" data-testid="waiting">
          {waiting} action{waiting === 1 ? '' : 's'} waiting to sync
          {online && <button onClick={sync}>Sync now</button>}
        </p>
      )}
      {problems.length > 0 && (
        <div className="rejected" role="alert">
          {problems.map((p) => (
            <p key={p.client_action_id}><strong>{p.label}</strong> was not applied: {p.reason}</p>
          ))}
          <button onClick={() => { clearRejected(); refreshCounts() }}>Dismiss</button>
        </div>
      )}

      <h2>My tasks ({tasks.filter((t) => t.status !== 'delivered').length})</h2>
      {tasks.length === 0 && <p>No tasks assigned in your wards.</p>}
      <ul className="tasks">
        {tasks.map((task) => (
          <li key={task.id} className={`task level-${task.urgency_level}`}>
            <div>
              <strong>{task.qty} × {task.kind}</strong> from {task.provider}
              <br />
              Incident {task.incident_id} · {task.category} · ward {task.ward ?? '?'} · urgency {task.urgency_level}
              {task.headcount ? ` · ${task.headcount} people` : ''}
              {task.lat != null && task.lon != null && (
                <> · <a href={`geo:${task.lat},${task.lon}`}>open in maps</a></>
              )}
            </div>
            <div className="task-actions">
              <span className="task-status">{task.status}</span>
              {task.status !== 'delivered' && (
                <button onClick={() => advance(task)}>{NEXT_STEP[task.status][1]}</button>
              )}
            </div>
          </li>
        ))}
      </ul>

      <h2>Report from the field</h2>
      <form onSubmit={report} className="field-report">
        <select value={category} onChange={(e) => setCategory(e.target.value)} aria-label="Category">
          {REPORT_CATEGORIES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={3}
          placeholder="What and where, e.g. Road blocked by a fallen tree near Hindmata" />
        <div>
          <button type="button" onClick={locate}>{position ? 'Location added ✓' : 'Add my location'}</button>
          <button type="submit">Send report</button>
        </div>
        {note && <p className="status" role="status">{note}</p>}
      </form>
    </section>
  )
}
