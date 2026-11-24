# AI contributions

Made of LLM (Gemini,claude) for help with parts of the project, mainly where a task was
technically difficult or where it saved repetitive work. The design decisions, major code
functionalities and the review of every change were done by the team. Every AI-assisted piece was
read, tested and run by us before it was merged.

The parts below are the challenging ones where AI help was used, grouped by the team member who
owns them.

## machine learning and evaluation

| File | Functions | Why it was challenging |
| --- | --- | --- |
| `backend/scripts/train_classifier.py` | `train()`, `export()` | Fitting MuRIL in a 4 GB GPU (frozen embeddings, fp16, resumable checkpoints) and exporting int8 ONNX that still agrees with the original model |
| `backend/scripts/evaluate.py` | `bcubed()`, `incident_outcomes()` | Scoring incident clustering against planted ground truth |
| `backend/scripts/api.py` | `sync()`, `apply_action()` | Applying queued offline actions exactly once and rejecting stale ones |

## backend

| File | Functions | Why it was challenging |
| --- | --- | --- |
| `backend/scripts/incidents.py` | `merge_drifted()` `detect_silent_zones()`| Spatial clustering with PostGIS DBSCAN, and finding areas that went quiet |
 backend/scripts/api.py | events(), create_allocation(), create_offer() | Streams live dashboard updates, records a coordinator's stock claim exactly once, and adds a located offer to its ward |
| `db/migrations/001_base.sql` | `event_log_chain()` | Hash-chained, append-only audit log in a database trigger |
| backend/scripts/load_base.py | osm_kind(), road_initialisms(), load_ward_places() | Classifies OpenStreetMap features, generates road short names and adds spoken ward names to the gazetteer |



## frontend

| File | Functions | Why it was challenging |
| --- | --- | --- |
| `frontend/src/outbox.ts` | `flush()` | Syncing queued phone actions safely when the network returns |
| `frontend/vite.config.ts` | service worker `runtimeCaching` | Caching the app and the map (with range requests) so they work offline |
| `frontend/src/MapView.tsx` | `basemapStyle()` | Serving the offline PMTiles basemap through MapLibre |

## Tooling and documentation

- **`demo.sh`** was generated with AI so that a new person can start the whole project with one
  command: it creates the secrets file, builds and starts the containers, loads the demo data and
  opens a tunnel for phones.
- **`backend/scripts/seed_demo.py`** (demo logins, reports and offers for a new database) was
  written with AI help.
- **`backend/tests/` (unit and pipeline tests for the backend) was
    written with AI help.
- **`README.md`**: AI was used for the format and structure of the README (sections, tables and
  the file-by-file guide) to make it easier to read.
