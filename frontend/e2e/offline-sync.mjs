// End-to-end offline check of the responder app (M7 demo) against the running compose stack.
//
// Needs: `docker compose up -d`, users demo_coord (coordinator) and demo_resp (responder) in
// the same wards with password demo-pass-123, and at least two open needs there (a replay provides
// them). Uses the system Chrome. Run: cd frontend && node e2e/offline-sync.mjs [screenshot dir]
//
// Flow: two accepted boat tasks -> responder goes offline -> reloads the app (service worker),
// dispatches both, files a report -> coordinator cancels one task -> reconnect -> outbox syncs:
// one applied, one rejected with a reason shown, report delivered once, outbox empty.
import { chromium } from 'playwright-core'
const BASE = 'http://localhost:8080'
const out = process.argv[2] ?? '.'

async function apiAs(user) {
  const r = await fetch(`${BASE}/api/auth/login`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ username: user, password: 'demo-pass-123' }) })
  const cookie = r.headers.get('set-cookie').split(';')[0]
  return async (path, body) => {
    const res = await fetch(BASE + path, { method: body ? 'POST' : 'GET', headers: { cookie, 'content-type': 'application/json' }, body: body ? JSON.stringify(body) : undefined })
    const json = await res.json(); if (!res.ok) throw new Error(path + ' ' + JSON.stringify(json)); return json
  }
}

const coord = await apiAs('demo_coord')
const now = Date.now()
const RUN = now.toString(36) // tags this run's offers so earlier runs never match
const tasks = []
const NEEDS = []
for (const incident of await coord('/api/incidents')) {
  const detail = await coord(`/api/incidents/${incident.id}`)
  for (const need of detail.needs) if (need.qty_open > 0 && NEEDS.length < 2) NEEDS.push(need)
}
if (NEEDS.length < 2) throw new Error('need two open needs in the coordinator\'s wards')
for (const need of NEEDS) {
  const { offer_id } = await coord('/api/offers', { kind: need.kind, provider: `Demo ${need.kind} ${RUN}-${need.id}`, qty: 1, lat: 19.012, lon: 72.843, available_from: new Date(now - 3600e3).toISOString(), available_until: new Date(now + 86400e3).toISOString(), verified: true })
  const { allocation_id } = await coord('/api/allocations', { need_id: need.id, offer_id, qty: 1, client_action_id: crypto.randomUUID() })
  await coord(`/api/allocations/${allocation_id}/accept`, {})
  tasks.push(allocation_id)
}
console.log('accepted tasks', tasks)

const browser = await chromium.launch({ channel: 'chrome', args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] })
const context = await browser.newContext({ viewport: { width: 400, height: 860 } })
const page = await context.newPage()
const errors = []
page.on('pageerror', (e) => errors.push('pageerror ' + e.message))
page.on('console', (m) => m.type() === 'error' && errors.push('console ' + m.text()))

await page.goto(`${BASE}/#responder`)
await page.fill('input[autocomplete="username"], input[name="username"], input >> nth=0', 'demo_resp')
await page.fill('input[type="password"]', 'demo-pass-123')
await page.click('button[type="submit"]')
await page.waitForSelector('.task')
await page.waitForFunction(() => navigator.serviceWorker?.controller != null, null, { timeout: 15000 }).catch(() => page.reload())
await page.waitForFunction(async () => !!(await (await caches.open('basemap')).match('/basemap/mumbai.pmtiles')), null, { timeout: 30000 })
console.log('online tasks shown:', await page.locator('.task').count(), '| SW controlling:', await page.evaluate(() => !!navigator.serviceWorker.controller))

// --- go offline: reload the app with no network, act on both tasks, file a report
await context.setOffline(true)
await page.reload()
await page.waitForSelector('.task')
console.log('offline reload ok, status:', await page.textContent('[data-testid=sync-status]'), '| tasks:', await page.locator('.task').count())
const tileOk = await page.evaluate(async () => (await fetch('/basemap/mumbai.pmtiles', { headers: { range: 'bytes=0-126' } })).status)
console.log('offline basemap range fetch status:', tileOk)
// Card text runs the provider straight into "Incident", hence the trailing I.
for (const id of tasks) await page.locator('.task', { hasText: `${RUN}-${NEEDS[tasks.indexOf(id)].id}I` }).getByRole('button').click()
await page.fill('.field-report textarea', 'Road blocked by a fallen tree near Hindmata')
await page.click('.field-report button[type=submit]')
console.log('waiting banner:', await page.textContent('[data-testid=waiting]'))
await page.screenshot({ path: `${out}/offline.png`, fullPage: true })

// --- meanwhile the coordinator cancels the second task on the server
await coord(`/api/allocations/${tasks[1]}/cancel`, {})

// --- reconnect: the outbox flushes on the online event
await context.setOffline(false)
await page.waitForFunction(() => !document.querySelector('[data-testid=waiting]'), null, { timeout: 15000 })
await page.waitForSelector('.rejected')
console.log('after sync status:', await page.textContent('[data-testid=sync-status]'))
console.log('rejected banner:', (await page.textContent('.rejected')).replace('Dismiss', ''))
console.log('task statuses now:', await page.locator('.task-status').allTextContents())
await page.screenshot({ path: `${out}/synced.png`, fullPage: true })
const outbox = await page.evaluate(() => localStorage.getItem('responder_outbox'))
console.log('outbox left:', outbox)
// Expected: requests that fail while offline, and the 401 from /api/auth/me before login.
const unexpected = errors.filter((e) => !e.includes('ERR_INTERNET_DISCONNECTED') && !e.includes('401'))
console.log('unexpected errors:', unexpected)
await browser.close()
const ok = outbox === '[]' && unexpected.length === 0
console.log(ok ? 'PASS' : 'FAIL')
process.exit(ok ? 0 : 1)
