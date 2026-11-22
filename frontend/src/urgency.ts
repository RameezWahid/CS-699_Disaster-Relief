// Sequential, light to dark: urgency 1 (low) to 5 (critical). Shared by the map and the lists.
export const URGENCY_COLORS = ['#fde68a', '#fbbf24', '#f97316', '#dc2626', '#7f1d1d']

// A silent zone is a cell that stopped reporting while the rest of the city kept going.
export const SILENT_RING = '#7c3aed'

export function quietMinutes(incident: { last_report_at: string }): number {
  return Math.max(0, Math.round((Date.now() - Date.parse(incident.last_report_at)) / 60_000))
}
