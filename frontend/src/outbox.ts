// Offline outbox for the responder app (spec 4.7, demo scope). Actions are kept in localStorage
// until the server has answered them, so a closed tab or a dropped connection loses nothing.
// Each action carries a client_action_id; resending one the server already saw is harmless.

export type ActionType = 'task_status' | 'report'

export interface OutboxAction {
  client_action_id: string
  type: ActionType
  payload: Record<string, unknown>
  base_status?: string
  client_ts: string
  label: string // shown in the UI while the action waits
}

export interface SyncResult {
  client_action_id: string
  result: 'applied' | 'duplicate' | 'rejected'
  original: 'applied' | 'rejected'
  reason: string | null
}

const OUTBOX_KEY = 'responder_outbox'
const REJECTED_KEY = 'responder_rejected'

function read<T>(key: string): T[] {
  try {
    return JSON.parse(localStorage.getItem(key) ?? '[]')
  } catch {
    return []
  }
}

export function pending(): OutboxAction[] {
  return read<OutboxAction>(OUTBOX_KEY)
}

export function rejected(): (OutboxAction & { reason: string })[] {
  return read(REJECTED_KEY)
}

export function clearRejected(): void {
  localStorage.removeItem(REJECTED_KEY)
}

export function deviceId(): string {
  let id = localStorage.getItem('device_id')
  if (!id) {
    id = crypto.randomUUID()
    localStorage.setItem('device_id', id)
  }
  return id
}

export function enqueue(action: Omit<OutboxAction, 'client_action_id' | 'client_ts'>): void {
  const full = { ...action, client_action_id: crypto.randomUUID(), client_ts: new Date().toISOString() }
  localStorage.setItem(OUTBOX_KEY, JSON.stringify([...pending(), full]))
}

let flushing = false

export type FlushOutcome = 'ok' | 'offline' | 'login'

// Send everything waiting. Answered actions leave the outbox; rejected ones are kept so the UI
// can say why. 'offline': server not reachable, try later. 'login': the session has expired.
export async function flush(): Promise<FlushOutcome> {
  const batch = pending()
  if (flushing || batch.length === 0) return 'ok'
  flushing = true
  try {
    const response = await fetch('/api/sync', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        device_id: deviceId(),
        actions: batch.map(({ label: _label, ...action }) => action),
      }),
    })
    if (response.status === 401) return 'login'
    if (!response.ok) return 'offline'
    const { results } = (await response.json()) as { results: SyncResult[] }
    const answered = new Map(results.map((r) => [r.client_action_id, r]))
    const newlyRejected = batch
      .filter((a) => answered.get(a.client_action_id)?.original === 'rejected')
      .map((a) => ({ ...a, reason: answered.get(a.client_action_id)?.reason ?? 'rejected' }))
    // Actions queued while this request was in flight stay in the outbox.
    const left = pending().filter((a) => !answered.has(a.client_action_id))
    localStorage.setItem(OUTBOX_KEY, JSON.stringify(left))
    localStorage.setItem(REJECTED_KEY, JSON.stringify([...rejected(), ...newlyRejected]))
    return 'ok'
  } catch {
    return 'offline'
  } finally {
    flushing = false
  }
}
