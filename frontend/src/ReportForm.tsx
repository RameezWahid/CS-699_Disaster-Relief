import { useState, type FormEvent } from 'react'

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

export default function ReportForm() {
  const [text, setText] = useState('')
  const [category, setCategory] = useState<string | null>(null)
  const [position, setPosition] = useState<Position | null>(null)
  const [photo, setPhoto] = useState<File | null>(null)
  const [status, setStatus] = useState('')

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
    const form = new FormData()
    form.append('client_msg_id', crypto.randomUUID())
    form.append('device_id', deviceId())
    form.append('text', text)
    if (category) form.append('category_hint', category)
    if (position) {
      form.append('lat', String(position.lat))
      form.append('lon', String(position.lon))
      form.append('accuracy_m', String(position.accuracy))
    }
    if (photo) form.append('photo', await compressPhoto(photo), 'photo.jpg')
    setStatus('Sending…')
    const response = await fetch('/api/reports', { method: 'POST', body: form })
    if (response.ok) {
      setText('')
      setCategory(null)
      setPhoto(null)
      setPosition(null)
      setStatus('Report received. Coordinators can now see it.')
    } else {
      const body = await response.json().catch(() => ({}))
      setStatus(`Not sent: ${body.detail ?? response.statusText}`)
    }
  }

  return (
    <form className="report-form" onSubmit={submit}>
      <h1>Report flooding</h1>
      <p className="hint">English, हिंदी, मराठी, or Hinglish. Say where, how many people, and what is needed.</p>
      <button type="button" onClick={locate} className="locate">
        {position ? `📍 Location set (±${Math.round(position.accuracy)} m)` : '📍 Use my location'}
      </button>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={5}
        maxLength={2000}
        placeholder="e.g. Hindmata ke paas 5 log phase hain, ek baccha bhi hai"
      />
      <div className="chips" role="group" aria-label="Category">
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
      <label className="photo">
        Photo (optional)
        <input type="file" accept="image/*" capture="environment"
          onChange={(e) => setPhoto(e.target.files?.[0] ?? null)} />
      </label>
      <button type="submit" className="send" disabled={!text.trim() && !position}>Send report</button>
      {status && <p className="status" role="status">{status}</p>}
    </form>
  )
}
