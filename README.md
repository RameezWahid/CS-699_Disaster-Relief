# Mumbai Monsoon Flood Relief Platform

Citizens report monsoon flooding from their phones in English, Hindi, Marathi or Hinglish. The
system reads each report (a fine-tuned MuRIL classifier plus safety rules), finds the place in
Mumbai, scores urgency and credibility, and groups reports into incidents on a live map.
Coordinators register relief resources (boats, food, medical teams, shelter beds); a matching
engine proposes the nearest suitable resource for the most urgent needs and never promises the
same stock twice. Responders work from an app that keeps working with no network and syncs each
action exactly once. Areas that suddenly stop reporting are flagged as silent zones.

**Research prototype. Not monitored. In a real emergency call 112.** All data is synthetic or test data.

Team7: Jay Gupta (ML and evaluation + backend ), Rameez Wahid ( backend + db ), Prathamesh Thakur (frontend + testing ).

---

## Run it on a new machine

### 1. Prerequisites

| Tool | Why |
| --- | --- |
| Docker with Compose v2 (`docker compose version`) | runs the database, API, worker and web app |
| `git`, `curl`, `openssl` | clone, health checks, generating secrets |

Everything else (PostgreSQL with PostGIS, Python and Node dependencies) is built inside the
containers. Ports 8080, 8000 and 5432 must be free.

### 2. Get the code

```sh
git clone https://github.com/RameezWahid/CS-699_Disaster-Relief.git
cd CS-699_Disaster-Relief
```

The repository includes everything the app needs to run: the 24 BMC ward boundaries, the Mumbai
OpenStreetMap extract for place names, 46 chronic flood spots, and the offline map
(`data/tiles/mumbai.pmtiles`, 6 MB).

**Optional, the trained classifier.** The model (`data/models/muril-flood/`: `model.onnx`,
`tokenizer.json`, `labels.json`, 240 MB) is not in Git because it is too large and is derived
from the HumAID dataset, whose licence forbids redistribution. Copy that folder from a team member,
or train it (see "Training the classifier"). Without it everything still runs; reports are then
categorised by the keyword rules alone.

### 3. Start everything with one command

```sh
./demo.sh
```

The script:

1. creates `.env` with random secrets if it does not exist;
2. builds and starts the containers (`docker compose up -d --build`; a few minutes the first time);
3. waits until the app answers;
4. adds demo data (`scripts/seed_demo.py`): three logins, 18 reports and three relief offers;
5. starts a temporary public HTTPS address for phones (Cloudflare quick tunnel; `cloudflared`
   is downloaded to `~/.local/bin` the first time, no account needed);
6. prints every address and the logins.

| Command | Does |
| --- | --- |
| `./demo.sh` | everything |
| `./demo.sh --no-tunnel` | laptop only |
| `./demo.sh --no-seed` | no demo data |
| `./demo.sh status` | services, health, current phone address |
| `./demo.sh stop` | stop the tunnel |
| `./demo.sh stop --all` | stop the tunnel and the containers (data is kept) |

`docker compose down -v` deletes all data; the next `./demo.sh` starts from a clean database.

### 4. Use it

| What | Where | Login |
| --- | --- | --- |
| Coordinator dashboard | http://localhost:8080 | `demo_coord` or `demo_admin` |
| Public report form | http://localhost:8080/#report | none |
| Responder app | http://localhost:8080/#responder | `demo_resp` |
| API documentation | http://localhost:8000/docs | |

Demo password: `demo-pass-123` (set `DEMO_PASSWORD` before the first `./demo.sh` to change it).
`demo_admin` sees every ward; `demo_coord` and `demo_resp` see wards F/S, G/N, H/E, H/W, K/E, L,
M/W and S.

**What the demo data shows** (within a minute of starting):

- a rescue incident at Hindmata confirmed by three different people, and one at Milan Subway;
- reports in Hinglish, English, romanised Marathi and Devanagari Marathi, each classified and placed;
- medical (Sion, Parel), food and water (Chembur), shelter (Dadar), roads blocked (Kurla, Andheri);
- matching proposals: boats and a medical team waiting in the Proposals tab;
- a **silent zone** at Milan Subway: four people reported 1.5 to 3 hours ago, then nothing,
  while the rest of the city kept reporting.

**On a phone.** Phones need HTTPS for the offline app and GPS, so use the tunnel address printed
by `./demo.sh` (`https://<random>.trycloudflare.com/#responder`). Open it once online, log in as
`demo_resp`, and wait 10 to 15 s so the app and the 6 MB map are stored. It then works in
airplane mode and syncs every queued action exactly once when the network returns. The address
changes every time the tunnel restarts, and anyone with it can reach the login page, so stop the
tunnel after a demo.

### 5. The same steps by hand

```sh
cp .env.example .env                 # replace every change-me, e.g. with: openssl rand -hex 32
docker compose up -d --build         # db, setup (migrations + Mumbai data), api, worker, web
docker compose restart web           # after any rebuild of api (nginx caches its address)
docker compose exec api python -m scripts.seed_demo          # optional demo data
docker compose exec api python -m scripts.api adduser <name> <responder|coordinator|admin> [wards...]
~/.local/bin/cloudflared tunnel --url http://localhost:8080   # optional, for phones
```

Useful: `docker compose logs -f worker` (reports being processed),
`docker compose exec db psql -U relief -d relief` (database shell).

---

## How a report flows

1. **Intake** (`api.py`, `intake.py`): the form posts to `/api/reports`; the device ID is stored only
   as a keyed hash, duplicates are ignored and each device is rate-limited. The message lands in
   the `inbox` table, which wakes the worker.
2. **Understanding** (`worker.py`): `text.py` normalises the text and fingerprints it;
   `extract.py` applies hard rules ("trapped", "drowning" always mean rescue, urgency 5) and pulls
   out headcount, needs and vulnerable people; `classifier.py` (MuRIL) decides the category when
   the rules find none; `geocode.py` places it (phone GPS first, else Mumbai place names);
   `triage.py` scores urgency 1 to 5.
3. **Incidents** (`incidents.py`): the report joins a nearby recent incident of the same kind or
   starts one. Credibility combines independent signals (distinct senders, GPS, photo, chronic
   flood spot, official alert, responder report). Low-credibility incidents wait in the Verify tab.
4. **Matching** (`matching.py`): each incident gets needs (for example 1 boat per 6 people). Every
   minute the worker proposes the nearest suitable offer for the most urgent needs; a coordinator
   accepts; responders in that ward see it as a task and mark it dispatched, then delivered.
5. **Every minute** (`worker.py`): merge incidents that drifted together, rescore (urgency grows
   with waiting time), detect silent zones, expire old proposals, propose new matches.

Every decision is written to a hash-chained audit log (`event_log`).

---

## What is distinctive in this implementation

Existing tools each cover one step (collecting reports, mapping, rain dashboards, volunteer
groups). This platform closes the loop from a raw message to confirmed delivery, and adds the
pieces below. Numbers are the values in the code.

### Finding the people who cannot report: silent zones (H3)

Mumbai is covered with Uber's **H3 hexagonal grid** at resolution 8 (683 equal cells of about
0.74 km²). Every minute (`incidents.py`, `detect_silent_zones`) a cell is flagged when at least
**3 different people** reported from it in the previous 2 hours, it has been **silent for 60
minutes**, and the rest of the city still sent **at least 10 reports** in that hour. The cell
becomes an urgency-5 `silent_zone` incident with a violet ring on the map. Quiet is treated as a
warning (dead phones, no power), not as safety. Hexagons are used because they have equal area
and equidistant neighbours, unlike wards, which vary greatly in size.

### Forwarded messages are not extra witnesses (SimHash)

Each report gets a 64-bit **SimHash** fingerprint over its words and word pairs (`text.py`).
A report within **6 bits** of one from the last 60 minutes is marked as a copy (`repeat_of`). A
copy joins the original's incident even if it carries a different location, and is **not counted
as an independent sender**, so fifty WhatsApp forwards of one rumour still count as one witness.

### One situation, one incident (grouping and DBSCAN merging)

A report joins an active incident of the same category group within **150 m** (widened by the
report's location uncertainty) that had a report in the last **60 minutes**. Every minute,
PostGIS `ST_ClusterDBSCAN` (eps 150 m, in UTM 43N metres, per category group) merges incidents
that drifted together: the oldest survives, reports and allocations move to it, and needs are
combined by the **larger** quantity, not the sum, because both described the same people.

### A Mumbai-specific geocoder

Text-only reports are placed using a gazetteer of **5,709 places** (5,639 from OpenStreetMap,
46 chronic flood spots compiled from cited news reports, 24 wards) with **8,698 names**,
including Marathi and Hindi names and the short forms people use ("SV Road", "JVLR"). Matching is
two-stage: PostgreSQL **pg_trgm** trigram similarity shortlists candidates using an index, then
**RapidFuzz** rescores them, weighted by place type (landmark 1.0, road 0.8, locality 0.6, ward
0.4). Two strong matches more than 1 km apart are flagged as ambiguous for a coordinator.

---

## Code organisation

```
CS-699_Disaster-Relief/
├── demo.sh                 one-command start (containers, demo data, tunnel)
├── docker-compose.yml      services: db, setup, api, worker, web
├── .env.example            secrets template (copied to .env, never committed)
├── backend/                Python: API, worker, NLP pipeline, matching, training and evaluation
│   ├── Dockerfile
│   ├── requirements.txt, requirements-dev.txt
│   ├── scripts/            all backend modules (run as python -m scripts.<name>)
│   └── tests/              pytest tests
├── frontend/               React + TypeScript dashboard, report form and offline responder app
│   ├── Dockerfile, nginx.conf
│   ├── src/                application code
│   ├── public/basemap/     offline map fonts, sprites and a link to the map tiles
│   └── e2e/                end-to-end offline test
├── db/migrations/          SQL schema, applied in order
├── data/                   base data (wards, OSM extract, flood spots, map tiles, gold set)
├── scenarios/, fixtures/   replay scenario of the 26 July 2005 flood and its rain data
```

### Backend: `backend/scripts/`

| File | Role |
| --- | --- |
| `api.py` | HTTP API (FastAPI): public report intake, login and roles, incidents, offers, allocations, alerts, responder tasks and offline sync, live updates (Server-Sent Events); `adduser` command |
| `worker.py` | Background worker: claims inbox messages and runs the pipeline; every minute merges, rescores, detects silent zones, expires and proposes matches |
| `intake.py` | Shared intake: sender pseudonymisation (HMAC), inbox insert, rate limiting |
| `text.py` | Text normalisation, language and script tagging, SimHash fingerprints for copies |
| `extract.py` | Rule-based extraction: hard rules, headcount, needs, vulnerability, rules-only category |
| `classifier.py` | Runs the fine-tuned MuRIL classifier (int8 ONNX, CPU) |
| `geocode.py` | Mumbai geocoder: GPS, or fuzzy matching of place names against the gazetteer |
| `triage.py` | Urgency (1 to 5) and credibility formulas and weights |
| `incidents.py` | Grouping reports into incidents, merging, rescoring, silent-zone detection |
| `matching.py` | Needs, race-safe claims, proposals, the allocation state machine, expiry |
| `config.py` | Central configuration (database, secrets, limits, paths) |
| `migrate.py` | Applies `db/migrations/` once each, in order |
| `load_base.py` | Loads wards, the H3 grid, chronic flood spots and OSM place names into a new database |
| `seed_demo.py` | Demo logins, 18 reports and three offers for a new database |
| `messages.py` | Message templates (5 language styles x 6 classes); every 4th is held out for replay |
| `train_templates.py` | Extra training-only templates, reviewed by fluent speakers |
| `synth.py` | Generates 12,000 synthetic labelled training messages from the templates |
| `prepare.py` | Builds train/validation/test sets from HumAID plus synthetic data |
| `train_classifier.py` | Fine-tunes MuRIL and exports the int8 ONNX model |
| `eval_humaid.py` | Scores the model on held-out real HumAID tweets |
| `metrics.py` | Precision, recall and F1 shared by training and evaluation |
| `replay.py` | Plays a scenario (e.g. the 2005 flood) into the real pipeline with known answers |
| `evaluate.py` | Scores a replay: classification, clustering, location, headcount, noise |
| `validate_gold.py` | Checks the hand-written gold set against its guidelines |

### Backend tests: `backend/tests/`

One test file per module (`test_text.py`, `test_extract.py`, `test_triage.py`, `test_classifier.py`,
`test_messages.py`, `test_synth.py`, `test_prepare.py`, `test_train_classifier.py`,
`test_eval_humaid.py`, `test_metrics.py`, `test_replay.py`, `test_evaluate.py`,
`test_scenario_s1.py`, `test_worker.py`); `test_geocode.py` and `test_pipeline.py` need the running
database (`RELIEF_E2E=1`).

```sh
pip install -r backend/requirements-dev.txt
cd backend && python -m pytest
```

### Frontend: `frontend/src/`

| File | Role |
| --- | --- |
| `main.tsx`, `App.tsx` | Entry point; switches between dashboard, report form and responder app; login state |
| `Login.tsx` | Login form |
| `Dashboard.tsx` | Coordinator dashboard: map, tabs (Queue, Verify, Proposals, Offers, Alerts), live updates |
| `MapView.tsx` | MapLibre map with the offline Mumbai basemap, incidents by urgency, wards and offers |
| `IncidentPanel.tsx` | Incident details: why this urgency, credibility signals, needs, allocations, messages |
| `OffersPanel.tsx` | Relief offers: list, add, withdraw |
| `AlertsPanel.tsx` | Official alerts for a ward or the whole city |
| `ReportForm.tsx` | Public report form (text, GPS, category, photo) |
| `Responder.tsx` | Offline-first responder app: tasks, dispatch and deliver, field reports |
| `outbox.ts` | On-phone outbox that syncs queued actions exactly once |
| `api.ts` | Typed API client and data types |
| `urgency.ts` | Urgency colours shared by the map and lists |
| `index.css` | All styles |

`vite.config.ts` builds the app and its service worker (offline support); `e2e/offline-sync.mjs`
tests the responder app offline in a real browser.

### Database: `db/migrations/`

| File | Adds |
| --- | --- |
| `001_base.sql` | wards, H3 cells, places and place names, inbox, reports, hash-chained audit log |
| `002_incidents.sql` | incidents and the link from reports |
| `003_matching.sql` | needs, offers, allocations |
| `004_intake_rate_limit.sql` | per-sender counts for rate limiting |
| `005_app_role.sql` | the restricted `relief_app` role used by the services |
| `006_verification.sql` | verification queue and fast track |
| `007_users.sql` | users, roles and wards |
| `008_coordination.sql` | app clock for replays, alerts, offer time windows, dispatch and delivery, offline sync log |

### Data

| Path | Contents | Source |
| --- | --- | --- |
| `data/base/BMC_Wards.geojson` | 24 BMC ward boundaries | DataMeet (CC BY-SA 2.5 IN) |
| `data/base/mumbai.osm.pbf` | Mumbai OpenStreetMap extract for place names | OpenStreetMap via Geofabrik (ODbL) |
| `data/gazetteer/` | 46 chronic flood spots and their news sources | team, cited per row |
| `data/tiles/mumbai.pmtiles` | offline basemap | Protomaps, OpenStreetMap (ODbL) |
| `data/gold/` | hand-written test messages and guidelines | team |
| `scenarios/`, `fixtures/` | 26 July 2005 replay scenario and rain | team; IMD, Open-Meteo |

Not in Git: `data/models/` (trained model), `data/raw/` and `data/training/` (HumAID, research
licence), `data/photos/` (report photos), `.env` (secrets).

---

## Training the classifier (optional)

Needs HumAID event set 1 (CrisisNLP, research licence) in `data/raw/events_set1/`, PyTorch and
Transformers, and preferably a GPU with 4 GB or more (about 1 hour on a GTX 1650).

```sh
pip install -r backend/requirements-dev.txt torch transformers
cd backend
python -m scripts.synth ../data/training/synthetic.csv     # 12,000 synthetic messages
python -m scripts.prepare                                  # train / validation / HumAID test
python -m scripts.train_classifier                         # fine-tune and export int8 ONNX
python -m scripts.eval_humaid                              # check on real tweets
```

The model lands in `data/models/muril-flood/`; `docker compose restart worker` loads it.

## Limitations

- Covers BMC Mumbai only; Navi Mumbai and Thane are outside the place list and map.
- Weather and tide data are not used yet.
