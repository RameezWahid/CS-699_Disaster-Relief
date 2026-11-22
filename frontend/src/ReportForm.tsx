import { useEffect, useState, type FormEvent } from 'react'

const CATEGORIES: [string, string][] = [
  ['rescue', 'Trapped / rescue'],
  ['medical', 'Medical'],
  ['food_water', 'Food / water'],
  ['shelter', 'Shelter'],
  ['road_blocked', 'Road blocked'],
]
// Phone photos are 3-10 MB; 1280 px at JPEG quality 0.7 is about 150-300 KB and still readable.
const PHOTO_MAX_SIDE = 1280
const PHOTO_QUALITY = 0.7

function deviceId(): string {
  let id = localStorage.getItem('device_id')
  if (!id) {
    id = crypto.randomUUID()
    localStorage.setItem('device_id', id)
  }
  return id
}

async function compressPhoto(file: File): Promise<Blob> {
  const bitmap = await createImageBitmap(file)
  const scale = Math.min(1, PHOTO_MAX_SIDE / Math.max(bitmap.width, bitmap.height))
  const canvas = document.createElement('canvas')
  canvas.width = Math.round(bitmap.width * scale)
  canvas.height = Math.round(bitmap.height * scale)
  canvas.getContext('2d')!.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
  return new Promise((resolve, reject) =>
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error('compression failed'))),
      'image/jpeg', PHOTO_QUALITY))
}

type Position = { lat: number; lon: number; accuracy: number }

// Offline outbox: every report is saved on the phone first and deleted only once the server has
// it. Retries reuse the same client_msg_id, and the server stores a repeated id once, so each
// report arrives exactly once however often the network drops.
// TODO(pwa): manifest + service worker so the app itself opens offline, and Background Sync
// so the outbox is sent even when the page is closed. Until then it is sent when this page is
// open and the phone comes back online.
type Queued = { id: string; queuedAt: number; fields: Record<string, string>; photo?: Blob }

function outbox<T>(mode: IDBTransactionMode, action: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    const open = indexedDB.open('relief', 1)
    open.onupgradeneeded = () => open.result.createObjectStore('outbox', { keyPath: 'id' })
    open.onerror = () => reject(open.error)
    open.onsuccess = () => {
      const request = action(open.result.transaction('outbox', mode).objectStore('outbox'))
      request.onsuccess = () => resolve(request.result)
      request.onerror = () => reject(request.error)
    }
  })
}

let flushing: Promise<string> | null = null

// Send queued reports oldest first. Returns a status line for the form.
function flushOutbox(): Promise<string> {
  flushing ??= (async () => {
    const queued = (await outbox<Queued[]>('readonly', (s) => s.getAll())).sort((a, b) => a.queuedAt - b.queuedAt)
    let sent = 0
    for (const item of queued) {
      const form = new FormData()
      for (const [key, value] of Object.entries(item.fields)) form.append(key, value)
      if (item.photo) form.append('photo', item.photo, 'photo.jpg')
      let response: Response
      try {
        response = await fetch('/api/reports', { method: 'POST', body: form })
      } catch {
        break // offline: keep the rest for later
      }
      // Busy or rate limited: try again later. Accepted or a duplicate: the server has it.
      if (response.status === 429 || response.status >= 500) break
      await outbox('readwrite', (s) => s.delete(item.id))
      if (response.ok) {
        sent += 1
      } else {
        // The server will never accept this one (e.g. invalid), so do not retry it forever.
        const body = await response.json().catch(() => ({}))
        return `Report dropped: ${body.detail ?? response.statusText}`
      }
    }
    const waiting = await outbox<number>('readonly', (s) => s.count())
    if (waiting) return `Saved on this phone. ${waiting} report(s) will be sent when you are back online.`
    return sent ? 'Report received. Coordinators can now see it.' : ''
  })().finally(() => { flushing = null })
  return flushing
}

export default function ReportForm() {
  const [text, setText] = useState('')
  const [category, setCategory] = useState<string | null>(null)
  const [position, setPosition] = useState<Position | null>(null)
  const [photo, setPhoto] = useState<File | null>(null)
  const [status, setStatus] = useState('')

  // Send anything left over from an earlier offline session, and again whenever the network returns.
  useEffect(() => {
    const send = () => { flushOutbox().then((s) => s && setStatus(s), () => {}) }
    send()
    window.addEventListener('online', send)
    return () => window.removeEventListener('online', send)
  }, [])

  function locate() {
    setStatus('Finding your location…')
    navigator.geolocation.getCurrentPosition(
      (p) => {
        setPosition({ lat: p.coords.latitude, lon: p.coords.longitude, accuracy: p.coords.accuracy })
        setStatus('')
      },
      (error) => setStatus(`Could not get location: ${error.message}. Describe the place instead.`),
      { enableHighAccuracy: true, timeout: 15000 },
    )
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    const fields: Record<string, string> = { client_msg_id: crypto.randomUUID(), device_id: deviceId(), text }
    if (category) fields.category_hint = category
    if (position) {
      fields.lat = String(position.lat)
      fields.lon = String(position.lon)
      fields.accuracy_m = String(position.accuracy)
    }
    const item: Queued = { id: fields.client_msg_id, queuedAt: Date.now(), fields }
    if (photo) item.photo = await compressPhoto(photo)
    // Save first, then send: a report is never lost if the network drops mid-request.
    await outbox('readwrite', (s) => s.put(item))
    setText('')
    setCategory(null)
    setPhoto(null)
    setPosition(null)
    setStatus('Sending…')
    setStatus(await flushOutbox())
  }

  const tone = /^(Report dropped|Could not)/.test(status) ? 'error'
    : status.startsWith('Report received') ? 'success' : 'info'

  return (
    <div className="page">
      <form className="card report-form" onSubmit={submit}>
        <header className="card-head">
          <h1>Report flooding</h1>
          <p className="hint">English, हिंदी, मराठी, or Hinglish. Say where, how many people, and what is needed.</p>
        </header>

        <div className="field">
          <span className="field-label"><b>1</b> Where are you?</span>
          <button type="button" onClick={locate} className={`locate${position ? ' set' : ''}`}>
            {position ? `📍 Location set (±${Math.round(position.accuracy)} m)` : '📍 Use my location'}
          </button>
        </div>

        <label className="field">
          <span className="field-label"><b>2</b> What is happening?</span>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={5}
            maxLength={2000}
            placeholder="e.g. Hindmata ke paas 5 log phase hain, ek baccha bhi hai"
          />
          <small className="count">{text.length} / 2000</small>
        </label>

        <div className="field">
          <span className="field-label" id="category-label"><b>3</b> What is needed? <em>optional</em></span>
          <div className="chips" role="group" aria-labelledby="category-label">
            {CATEGORIES.map(([value, label]) => (
              <button
                type="button"
                key={value}
                aria-pressed={category === value}
                onClick={() => setCategory(category === value ? null : value)}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        <div className="field">
          <span className="field-label"><b>4</b> Photo <em>optional</em></span>
          {photo ? (
            <div className="photo-picked">
              <span>🖼️ {photo.name}</span>
              <button type="button" className="link" onClick={() => setPhoto(null)}>Remove</button>
            </div>
          ) : (
            <label className="photo-drop">
              <input type="file" accept="image/*" capture="environment"
                onChange={(e) => setPhoto(e.target.files?.[0] ?? null)} />
              📷 Take or choose a photo
            </label>
          )}
        </div>

        <button type="submit" className="btn btn-primary btn-block" disabled={!text.trim() && !position}>Send report</button>
        {status && <p className={`notice ${tone}`} role="status">{status}</p>}
      </form>
    </div>
  )
}
