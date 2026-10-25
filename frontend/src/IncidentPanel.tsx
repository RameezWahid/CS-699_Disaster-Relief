import { useEffect, useState } from 'react'
import { api, type IncidentDetail, type Me, type Need, type Offer } from './api.ts'

const SIGNAL_LABELS: Record<string, string> = {
  senders: 'distinct senders', gps: 'GPS pin', photo: 'photo', heavy_rain: 'heavy rain nearby',
  chronic_spot: 'chronic flood spot', official_alert: 'official alert', responder: 'responder report',
}

function AllocateForm({ need, offers, onDone }: { need: Need; offers: Offer[]; onDone: (message: string) => void }) {
  const compatible = offers.filter((o) => o.kind === need.kind && o.qty_available > 0)
  const [offerId, setOfferId] = useState(compatible[0]?.id ?? 0)
  const [qty, setQty] = useState(1)
  if (!compatible.length) return <small className="hint">No active {need.kind.replace('_', ' ')} offers.</small>
  const offer = compatible.find((o) => o.id === offerId) ?? compatible[0]

  async function allocate() {
    try {
      // A fresh id per click; a network retry of the same click would reuse it.
      await api('/api/allocations', { body: { need_id: need.id, offer_id: offer.id, qty, client_action_id: crypto.randomUUID() } })
      onDone(`Proposed ${qty} from ${offer.provider}`)
    } catch (e) {
      onDone(e instanceof Error ? e.message : 'allocation failed')
    }
  }

  return (
    <span className="allocate">
      <select value={offer.id} onChange={(e) => setOfferId(Number(e.target.value))}>
        {compatible.map((o) => <option key={o.id} value={o.id}>{o.provider} ({o.qty_available} free)</option>)}
      </select>
      <input type="number" min={1} max={Math.min(need.qty_open, offer.qty_available)} value={qty}
        onChange={(e) => setQty(Number(e.target.value))} aria-label="quantity" />
      <button onClick={allocate}>Allocate</button>
    </span>
  )
}

interface Props {
  id: number
  me: Me
  offers: Offer[]
  version: number
  onClose: () => void
  onChanged: () => void
}

export default function IncidentPanel({ id, me, offers, version, onClose, onChanged }: Props) {
  const [incident, setIncident] = useState<IncidentDetail | null>(null)
  const [message, setMessage] = useState('')

  useEffect(() => {
    api<IncidentDetail>(`/api/incidents/${id}`).then(setIncident, (e) => setMessage(e.message))
  }, [id, version])

  async function setStatus(status: 'verified' | 'dismissed' | 'resolved') {
    try {
      await api(`/api/incidents/${id}/status`, { body: { status } })
      setMessage(`Marked ${status}`)
      onChanged()
    } catch (e) {
      setMessage(e instanceof Error ? e.message : 'update failed')
    }
  }

  if (!incident) return <div className="panel-body"><button className="link" onClick={onClose}>← Back</button><p>{message || 'Loading…'}</p></div>
  const canDecide = me.role !== 'responder'
  const factors = incident.urgency.factors ?? {}

  return (
    <div className="panel-body incident">
      <button className="link" onClick={onClose}>← Back to lists</button>
      <h2>#{incident.id} {incident.category.replace('_', ' ')} · urgency {incident.urgency_level}</h2>
      <p className="meta">
        Ward {incident.ward ?? 'unknown'} · status {incident.status} · {incident.report_count} reports from {incident.sender_count} senders
        {incident.headcount !== null && <> · {incident.headcount} people{incident.headcount_conflict && <span className="tag warn">reports disagree</span>}</>}
      </p>
      {message && <p className="status" role="status">{message}</p>}
      {canDecide && (
        <div className="actions">
          {incident.status === 'open' && <button className="primary" onClick={() => setStatus('verified')}>Verify</button>}
          <button onClick={() => setStatus('resolved')}>Resolve</button>
          <button onClick={() => setStatus('dismissed')}>Dismiss as false</button>
        </div>
      )}

      <h3>Why urgency {incident.urgency_level}</h3>
      <table className="factors"><tbody>
        {Object.entries(factors).map(([name, value]) => <tr key={name}><td>{name}</td><td>{value.toFixed(2)}</td></tr>)}
        <tr><td>score S</td><td>{incident.urgency.S}</td></tr>
        {incident.urgency.override && <tr><td>override</td><td>{incident.urgency.override} → level 5</td></tr>}
      </tbody></table>

      <h3>Credibility {incident.credibility.toFixed(2)}{incident.credibility < 0.5 && <span className="tag warn">verify</span>}</h3>
      <ul className="signals">
        {Object.entries(incident.credibility_signals).filter(([k]) => k in SIGNAL_LABELS).map(([name, value]) => (
          <li key={name} className={value ? 'on' : 'off'}>{SIGNAL_LABELS[name]}{typeof value === 'number' ? `: ${value}` : ''}</li>
        ))}
        {'rain_mm_per_h' in incident.credibility_signals && <li className="off">rain {incident.credibility_signals.rain_mm_per_h} mm/h</li>}
      </ul>

      <h3>Needs</h3>
      {incident.needs.length ? incident.needs.map((need) => (
        <div className="need" key={need.id}>
          <span>{need.kind.replace('_', ' ')}: {need.qty_open} open of {need.qty_total}</span>
          {canDecide && need.qty_open > 0 && <AllocateForm need={need} offers={offers} onDone={(m) => { setMessage(m); onChanged() }} />}
        </div>
      )) : <p className="hint">No resource needs extracted.</p>}

      {incident.allocations.length > 0 && <>
        <h3>Allocations</h3>
        <ul className="plain">
          {incident.allocations.map((a) => <li key={a.id}>{a.qty} × {a.kind.replace('_', ' ')} from {a.provider}: <strong>{a.status}</strong></li>)}
        </ul>
      </>}

      <h3>Messages</h3>
      {incident.reports.map((r) => (
        <blockquote key={r.id} className={r.repeat_of ? 'repeat' : ''}>
          <p>{r.text}</p>
          <small>
            {new Date(r.received_at).toLocaleTimeString()} · {r.source} · {r.lang}
            {r.extraction.location.name && ` · ${r.extraction.location.name}`}
            {r.has_gps && ' · GPS'}{r.has_photo && ' · photo'}{r.repeat_of && ' · copy of an earlier message'}
          </small>
        </blockquote>
      ))}
    </div>
  )
}
