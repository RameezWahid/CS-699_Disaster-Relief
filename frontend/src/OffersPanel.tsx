import { useState, type FormEvent } from 'react'
import { api, OFFER_KINDS, type Offer } from './api.ts'

function localInput(date: Date): string {
  const offset = date.getTimezoneOffset() * 60000
  return new Date(date.getTime() - offset).toISOString().slice(0, 16)
}

export default function OffersPanel({ offers, onChanged }: { offers: Offer[]; onChanged: () => void }) {
  const [form, setForm] = useState(() => {
    const now = new Date()
    return {
      kind: 'boat', provider: '', qty: 1, lat: '19.0176', lon: '72.8562', verified: false,
      from: localInput(now), until: localInput(new Date(now.getTime() + 12 * 3600_000)),
    }
  })
  const [message, setMessage] = useState('')

  async function submit(event: FormEvent) {
    event.preventDefault()
    try {
      await api('/api/offers', {
        body: {
          kind: form.kind, provider: form.provider, qty: form.qty, lat: Number(form.lat), lon: Number(form.lon),
          verified: form.verified, available_from: new Date(form.from).toISOString(),
          available_until: new Date(form.until).toISOString(),
        },
      })
      setMessage('Offer added')
      setForm({ ...form, provider: '' })
      onChanged()
    } catch (e) {
      setMessage(e instanceof Error ? e.message : 'could not add offer')
    }
  }

  async function withdraw(id: number) {
    await api(`/api/offers/${id}/withdraw`, { method: 'POST' }).then(onChanged, (e) => setMessage(e.message))
  }

  return (
    <>
      <table className="offers"><thead><tr><th>Kind</th><th>Provider</th><th>Free</th><th>Ward</th><th /></tr></thead><tbody>
        {offers.map((o) => (
          <tr key={o.id}>
            <td>{o.kind.replace('_', ' ')}</td>
            <td>{o.provider}{o.verified && <span className="tag ok">verified</span>}{o.synthetic && <span className="tag">synthetic</span>}</td>
            <td>{o.qty_available}/{o.qty_total}</td>
            <td>{o.ward ?? '-'}</td>
            <td><button className="link" onClick={() => withdraw(o.id)}>Withdraw</button></td>
          </tr>
        ))}
      </tbody></table>
      <form className="offer-form" onSubmit={submit}>
        <h3>Add an offer</h3>
        <label>Kind<select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
          {OFFER_KINDS.map((k) => <option key={k} value={k}>{k.replace('_', ' ')}</option>)}
        </select></label>
        <label>Provider<input required minLength={2} value={form.provider} onChange={(e) => setForm({ ...form, provider: e.target.value })} placeholder="e.g. Fire brigade, Byculla" /></label>
        <label>Quantity<input type="number" min={1} value={form.qty} onChange={(e) => setForm({ ...form, qty: Number(e.target.value) })} /></label>
        <span className="pair">
          <label>Latitude<input value={form.lat} onChange={(e) => setForm({ ...form, lat: e.target.value })} /></label>
          <label>Longitude<input value={form.lon} onChange={(e) => setForm({ ...form, lon: e.target.value })} /></label>
        </span>
        <span className="pair">
          <label>From<input type="datetime-local" value={form.from} onChange={(e) => setForm({ ...form, from: e.target.value })} /></label>
          <label>Until<input type="datetime-local" value={form.until} onChange={(e) => setForm({ ...form, until: e.target.value })} /></label>
        </span>
        <label className="check"><input type="checkbox" checked={form.verified} onChange={(e) => setForm({ ...form, verified: e.target.checked })} />Verified provider</label>
        <button type="submit" className="primary">Add offer</button>
        {message && <p className="status" role="status">{message}</p>}
      </form>
    </>
  )
}
