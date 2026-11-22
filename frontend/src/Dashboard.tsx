import { useCallback, useEffect, useRef, useState } from 'react'
import AlertsPanel from './AlertsPanel.tsx'
import { api, type Allocation, type Incident, type Me, type Offer } from './api.ts'
import IncidentPanel from './IncidentPanel.tsx'
import MapView from './MapView.tsx'
import OffersPanel from './OffersPanel.tsx'
import { URGENCY_COLORS } from './urgency.ts'

type Tab = 'queue' | 'verify' | 'proposals' | 'offers' | 'alerts'
// Bursts of events (a replay at 60x) collapse into one refetch.
const REFRESH_DEBOUNCE_MS = 400

// TODO(silent): show category 'silent_zone' as "went quiet, go and check" with its own icon,
// using urgency.silent (h3, senders_before, city_reports); it has 0 reports and no credibility.
function IncidentRow({ incident, onSelect }: { incident: Incident; onSelect: () => void }) {
  return (
    <button className="row" onClick={onSelect}>
      <span className="level" style={{ background: URGENCY_COLORS[incident.urgency_level - 1] }}>{incident.urgency_level}</span>
      <span className="row-main">
        <span>
          <strong>{incident.category.replace('_', ' ')}</strong>
          {incident.headcount !== null && ` · ${incident.headcount} people`}
          {incident.urgency.override && <span className="tag">{incident.urgency.override}</span>}
        </span>
        <small>
          ward {incident.ward ?? '?'} · {incident.report_count} report{incident.report_count > 1 ? 's' : ''} ·
          {' '}waiting {incident.waiting_min} min · credibility {incident.credibility.toFixed(2)}
        </small>
      </span>
    </button>
  )
}

export default function Dashboard({ me, onLogout }: { me: Me; onLogout: () => void }) {
  const [tab, setTab] = useState<Tab>('queue')
  const [incidents, setIncidents] = useState<Incident[]>([])
  const [verifyQueue, setVerifyQueue] = useState<Incident[]>([])
  const [proposals, setProposals] = useState<Allocation[]>([])
  const [offers, setOffers] = useState<Offer[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [version, setVersion] = useState(0) // bumps on every refresh so open panels reload too
  const [error, setError] = useState('')
  const timer = useRef<number | undefined>(undefined)

  const refresh = useCallback(async () => {
    try {
      const [all, verify, proposed, active] = await Promise.all([
        api<Incident[]>('/api/incidents'),
        api<Incident[]>('/api/incidents?queue=verify'),
        api<Allocation[]>('/api/allocations?status=proposed'),
        me.role === 'responder' ? Promise.resolve([]) : api<Offer[]>('/api/offers'),
      ])
      setIncidents(all)
      setVerifyQueue(verify)
      setProposals(proposed)
      setOffers(active)
      setVersion((v) => v + 1)
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'refresh failed')
    }
  }, [me.role])

  useEffect(() => {
    timer.current = window.setTimeout(refresh, 0) // first load, scheduled like later refreshes
    // The stream is scoped to this user's wards server-side; any event means "something changed".
    const events = new EventSource('/api/events')
    const schedule = () => {
      window.clearTimeout(timer.current)
      timer.current = window.setTimeout(refresh, REFRESH_DEBOUNCE_MS)
    }
    for (const kind of ['report.created', 'incident.created', 'incident.updated', 'incident.merged',
      'incident.verified', 'incident.dismissed', 'incident.resolved', 'allocation.proposed',
      'allocation.accepted', 'allocation.dispatched', 'allocation.delivered', 'allocation.cancelled',
      'offer.created', 'offer.withdrawn', 'alert.raised', 'alert.ended']) {
      events.addEventListener(kind, schedule)
    }
    // Waiting times grow and proposals expire even when nothing new arrives.
    const tick = window.setInterval(refresh, 60_000)
    return () => {
      events.close()
      window.clearInterval(tick)
      window.clearTimeout(timer.current)
    }
  }, [refresh])

  async function decide(allocationId: number, action: 'accept' | 'cancel') {
    try {
      await api(`/api/allocations/${allocationId}/${action}`, { method: 'POST' })
      refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : `${action} failed`)
    }
  }

  const tabs: [Tab, string][] = [
    ['queue', `Queue (${incidents.length})`],
    ['verify', `Verify (${verifyQueue.length})`],
    ['proposals', `Proposals (${proposals.length})`],
    ...(me.role === 'responder' ? [] : [['offers', `Offers (${offers.length})`] as [Tab, string]]),
    ['alerts', 'Alerts'],
  ]

  return (
    <div className="dashboard">
      <MapView incidents={incidents} offers={offers} myWards={me.wards} selectedId={selectedId} onSelect={setSelectedId} />
      <aside className="panel">
        <header className="panel-head">
          <span>{me.username} · {me.role} · {me.wards.length ? `wards ${me.wards.join(', ')}` : 'all wards'}</span>
          <button className="link" onClick={onLogout}>Log out</button>
        </header>
        {error && <p className="error" role="alert">{error}</p>}
        {selectedId !== null ? (
          <IncidentPanel id={selectedId} me={me} offers={offers} version={version}
            onClose={() => setSelectedId(null)} onChanged={refresh} />
        ) : (
          <>
            <nav className="subtabs">
              {tabs.map(([key, label]) => (
                <button key={key} aria-pressed={tab === key} onClick={() => setTab(key)}>{label}</button>
              ))}
            </nav>
            <div className="panel-body">
              {tab === 'queue' && (incidents.length ? incidents.map((i) => (
                <IncidentRow key={i.id} incident={i} onSelect={() => setSelectedId(i.id)} />
              )) : <p className="hint">No active incidents in your wards.</p>)}
              {tab === 'verify' && (verifyQueue.length ? verifyQueue.map((i) => (
                <IncidentRow key={i.id} incident={i} onSelect={() => setSelectedId(i.id)} />
              )) : <p className="hint">Nothing waiting for verification. Urgency-5 items appear first when present.</p>)}
              {tab === 'proposals' && (proposals.length ? proposals.map((a) => (
                <div className="row static" key={a.id}>
                  <span className="level" style={{ background: URGENCY_COLORS[a.urgency_level - 1] }}>{a.urgency_level}</span>
                  <span className="row-main">
                    <span>
                      <strong>{a.qty} × {a.kind.replace('_', ' ')}</strong> from {a.provider}
                      {a.verified && <span className="tag ok">verified</span>}
                    </span>
                    <small>
                      incident #{a.incident_id} · ward {a.ward} · {(a.distance_m / 1000).toFixed(1)} km ·
                      {' '}expires in {a.expires_in_min} min
                    </small>
                    {me.role !== 'responder' && (
                      <span className="actions">
                        <button className="primary" onClick={() => decide(a.id, 'accept')}>Accept</button>
                        <button onClick={() => decide(a.id, 'cancel')}>Reject</button>
                        <button className="link" onClick={() => setSelectedId(a.incident_id)}>View incident</button>
                      </span>
                    )}
                  </span>
                </div>
              )) : <p className="hint">No pending proposals. The matcher proposes once a minute.</p>)}
              {tab === 'offers' && <OffersPanel offers={offers} onChanged={refresh} />}
              {tab === 'alerts' && <AlertsPanel me={me} />}
            </div>
          </>
        )}
      </aside>
    </div>
  )
}
