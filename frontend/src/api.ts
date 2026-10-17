// Typed wrappers for the coordination API. The session lives in an httpOnly cookie, so plain
// same-origin fetch carries it; nothing token-related is stored in JavaScript.

export interface Me {
  username: string
  role: 'responder' | 'coordinator' | 'admin'
  wards: string[]
}

export interface Urgency {
  S: number
  factors: Record<string, number>
  base_level: number
  level: number
  override: string | null
}

export interface Incident {
  id: number
  lon: number | null
  lat: number | null
  category: string
  status: string
  ward: string | null
  headcount: number | null
  headcount_conflict: boolean
  urgency_level: number
  urgency: Urgency
  credibility: number
  credibility_signals: Record<string, number | boolean>
  report_count: number
  sender_count: number
  first_report_at: string
  last_report_at: string
  waiting_min: number
}

export interface Report {
  id: number
  received_at: string
  source: string
  text: string
  lang: string
  category: string
  headcount: number | null
  needs: string[]
  vulnerability: string[]
  has_gps: boolean
  has_photo: boolean
  repeat_of: number | null
  extraction: { location: { name: string | null; source: string | null; confidence: number; uncertainty_m: number | null } }
}

export interface Need { id: number; kind: string; qty_total: number; qty_open: number }

export interface Allocation {
  id: number
  status: string
  qty: number
  expires_at: string
  expires_in_min: number
  kind: string
  need_id: number
  incident_id: number
  ward: string | null
  urgency_level: number
  offer_id: number
  provider: string
  verified: boolean
  distance_m: number
}

export interface IncidentDetail extends Incident {
  reports: Report[]
  needs: Need[]
  allocations: { id: number; need_id: number; offer_id: number; provider: string; kind: string; qty: number; status: string; expires_at: string }[]
}

export interface Offer {
  id: number
  kind: string
  provider: string
  qty_total: number
  qty_available: number
  lon: number
  lat: number
  ward: string | null
  available_from: string
  available_until: string
  verified: boolean
  synthetic: boolean
}

export interface Alert { id: number; source: string; title: string; ward: string | null; starts_at: string; ends_at: string }

export const OFFER_KINDS = ['blanket', 'boat', 'food', 'medical_team', 'medicine', 'shelter_bed', 'water']

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export async function api<T>(path: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
  const response = await fetch(path, {
    method: init.method ?? (init.body === undefined ? 'GET' : 'POST'),
    headers: init.body === undefined ? undefined : { 'content-type': 'application/json' },
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
  })
  if (!response.ok) {
    const detail = await response.json().then((b) => b.detail, () => response.statusText)
    throw new ApiError(response.status, typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  return response.json()
}
