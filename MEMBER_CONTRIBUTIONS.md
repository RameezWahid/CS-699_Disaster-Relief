# Member contributions

Team 7: Jay Gupta, Rameez Wahid, Prathamesh Thakur.

## a) Percentage contribution

| Member | Area | Contribution |
| --- | --- | --- |
| Jay Gupta | ML and evaluation, backend | 33% |
| Rameez Wahid | Backend, database | 33% |
| Prathamesh Thakur | Frontend, testing | 33% |

All three members agree on an equal split; there are no differing opinions.

## b) Each member's contribution

### Jay Gupta (ML and evaluation, backend)

I built the language and machine-learning side of the platform, which turns a free-text flood
message into structured fields. I wrote the text layer: normalisation for mixed Hindi, Marathi and
English (Roman and Devanagari scripts), language tagging, and the SimHash fingerprint used to spot
forwarded copies. On top of it I wrote the rule-based extraction: hard rescue rules (trapped,
drowning, not breathing) that always force a rescue category, headcount from words and numerals
in all three languages, needs, and vulnerability (children, elderly, pregnant, dialysis). I also
wrote the urgency formula and the noisy-OR credibility score that the incident logic uses.

I built the geocoder that finds where a report is from. It matches spans of the message against a
gazetteer of Mumbai places, weights them by kind (chronic flood spot, landmark, road, locality, ward), flags a place
name that is ambiguous instead of guessing, and falls back to the phone's GPS. I documented the
sources and coordinates of the chronic flood spots. For evaluation I wrote the gold-set guidelines
and templates, the 300-message team gold draft and a validator for it.

I trained the classifier that takes over when the rules find no category. I wrote multilingual
message templates with a held-out split for replay, a synthetic message generator over real
gazetteer places, and the HumAID label mapping with train, validation and test preparation. I
fine-tuned MuRIL on a 4 GB GPU (frozen embeddings, fp16, resumable checkpoints), exported it to an
int8 ONNX model that runs on a CPU, and checked that the exported model agrees with the original. It
reaches a macro-F1 of 0.98 on the validation split, and the worker falls back to the rules when no
model is installed. I added a separate evaluation on held-out HumAID data.

To test the whole system rather than single functions, I wrote per-class precision, recall, F1 and
macro-F1 metrics, the S1 cloudburst scenario, a replay tool that feeds a scenario into the inbox
with its ground truth planted, and an evaluation harness that scores the result (clustering quality
with B-cubed, and whether each planted incident was found and served).

On the backend I wrote the responder task list and the offline sync endpoint, which applies actions
queued on a phone exactly once and rejects actions on a task that changed in the meantime.

### Rameez Wahid (backend, database)

I built the backend of the platform, from the empty repository to the working pipeline. I set up the
project structure, the Docker Compose stack (database, setup, API, worker, web) and the migration
runner, and designed the PostgreSQL/PostGIS schema: wards, the H3 cell grid, places, the inbox
queue, reports, incidents, needs, offers, allocations and users. I built the intake path, where
reports are queued, claimed by a background worker, turned into stored reports and logged, with
deduplication and per-sender rate limiting. Because a report is saved before any processing,
nothing is lost if the worker crashes, and a resent message is stored only once.

I implemented the core decision logic. Reports are grouped into incidents with a 150 m / 60 minute
join rule, and forwarded copies are detected with SimHash. A forwarded copy joins the original's
incident but is not counted as an extra witness, so fifty forwards of one rumour still count as one
sender. When incidents are merged, their needs are combined by the larger amount rather than the
sum, because both describe the same people. Urgency and credibility are scored separately: urgency
is a weighted score that keeps rising while a request waits, and credibility is over independent
evidence (distinct senders, GPS, photo, chronic flood spot, official alert, responder report). This
drives the verification queue and fast-tracking, so a doubtful but life-threatening report is acted
on while it is being checked instead of being buried.

I wrote the matching engine. It proposes the nearest suitable offer for the most urgent needs,
searching outward from 1 km to 30 km, reserves stock with atomic conditional updates backed by
database constraints, and moves allocations through a state machine from proposal to dispatch to
delivery, with boats and medical teams returning to the pool after each delivery.

I built the API the frontend depends on: login with roles and ward-scoped access, incidents and the
verification queue, offers, allocations, alerts and responder tasks.

### Prathamesh Thakur (frontend, testing)

I built the frontend of the platform in React and TypeScript with Vite, from the first login page to
the coordinator dashboard and the responder app. I wrote the login page, where the session is kept
in an httpOnly cookie so no token is stored in JavaScript and each role sees its own view, and the
citizen report form, which takes the phone's GPS position, lets the citizen pick a category, and
shrinks a 3-10 MB phone photo to about 200 KB before upload so it gets through on a weak connection.

I built the map view with MapLibre on an offline PMTiles basemap of Mumbai, with its fonts and
sprites bundled in the app, so the map loads without any internet tile server. Incidents are drawn
as circles coloured on a shared five-step urgency scale, with a heatmap layer for dense areas. Around
the map I built the coordinator dashboard: the incident panel (details, the verification queue and
silent-zone alerts), the offers panel, the alerts panel where a coordinator records an official
warning for a ward, and the proposal actions (accept, reject, dispatch). The dashboard updates live
from the server's event stream, and a burst of events is collapsed into a single refresh.

I built the offline-first responder app for phones. Tasks are cached on the device, every action
goes into an outbox that survives a closed tab or a dropped network, and the outbox is sent when the
connection returns, when the app regains focus and when it loads. The app is installable, and its
service worker caches the app and the map (with range requests for the PMTiles file) so it works
with no signal. Finally I redesigned the citizen form and the responder app as cards with numbered
fields, a character count, a photo picker, coloured status notices and dark-mode support, and fixed
the build configuration after a merge left duplicate entries in it.

## c) GitHub logs

Repository: https://github.com/RameezWahid/CS-699_Disaster-Relief (all branches). Each list shows the
commits authored by that member, newest first: commit hash, date and time (IST), message.

### Jay Gupta

```
  e679d2a  2026-11-24 19:03  Prepare submission: README, AI contributions, demo script and data, offline map
  c267320  2026-11-22 22:36  Add responder task list and offline sync endpoints
  1200675  2026-11-22 21:57  Add PyYAML for scenario replay and evaluation
  c1e1f8d  2026-11-22 19:30  Add evaluation harness that scores a replay against planted truth
  7469c40  2026-11-22 18:34  Add training-only templates to widen classifier phrasing
  e38617b  2026-11-22 17:36  Add scenario replay that plants ground truth in the inbox
  514de59  2026-11-22 17:30  Add S1 cloudburst scenario and 26 July 2005 rain fixture
  d1e02de  2026-11-22 14:18  Add held-out HumAID evaluation for the exported classifier
  e54f8e8  2026-11-19 22:28  Add resumable MuRIL fine-tuning with int8 ONNX export check
  882bb00  2026-11-17 12:01  Add int8 ONNX MuRIL classifier with rule-based fallback
  76069d3  2026-11-17 00:30  Add HumAID label mapping and train/val/test preparation
  973ce9a  2026-11-15 20:57  Add synthetic training message generator over gazetteer places
  76e6cde  2026-11-14 13:26  Add multilingual flood message templates with held-out replay split
  1481cca  2026-11-13 14:50  Add per-class precision, recall, F1 and macro-F1 metrics
  b2ff8d0  2026-11-06 00:07  Merge remote-tracking branch 'origin/feature_jay' into feature_jay
  574620d  2026-11-06 00:07  Add team gold draft (300 messages) and coordinates to re-check
  2caf2ca  2026-11-06 00:07  Add gold-set validator and document how to run it
  9661222  2026-11-06 00:07  Add sources and coordinate notes for the chronic flood spots
  168368e  2026-11-06 00:07  Add Mumbai geocoder: gazetteer span matching, kind weights, ambiguity, GPS fallback
  cd797c8  2026-10-17 22:29  Merge pull request #4 from RameezWahid/feature_jay
  02d3bec  2026-10-17 21:46  Add gold-set writing guidelines and templates
  c0c2389  2026-10-17 21:46  Add urgency and noisy-OR credibility scoring
  f54961b  2026-10-17 21:46  Add rule-based extraction: hard rescue rules, headcount, needs, vulnerability
  28040e1  2026-10-17 21:46  Add text normalisation, language tagging, and SimHash near-duplicate check
```

### Rameez Wahid

```
  42938bb  2026-11-23 13:13  Add co-ordination for responder app
  dad3120  2026-11-23 04:32  Add the configuration API
  e559ab2  2026-11-23 04:03  Load OpenStreetMap places into the gazetteer
  335b163  2026-11-22 22:32  Add frontend for silent zone detection
  cf72008  2026-11-22 19:51  Add offline responder
  e9643e3  2026-11-22 18:24  Added geocoder + classifier in the worker
  8326f03  2026-11-20 03:51  Add Silent-Zone Detection
  082c4ae  2026-11-18 21:44  Add minimal dashboard API and login
  bd91ab4  2026-11-18 02:32  Add incident credibility, verification queue and fast-track
  f01cda8  2026-11-18 02:32  Add incident credibility, verification queue and fast-track -->(rebase)
  a4b86f5  2026-11-18 02:04  Add requirememts + set a proxy API
  ebe7d39  2026-11-16 16:00  Adding worker and Testing Pipeline
  dd4f788  2026-11-12 00:04  Adding API + dockerFile
  c0ed098  2026-10-28 03:51  Align backend modules with shared category and place-name logic
  3cd9b61  2026-10-26 00:05  Add matching engine and schema
  4bdb869  2026-10-22 23:35  Add incident schema and report grouping
  40803c1  2026-10-17 22:37  added fix for the .gitignore files
  768f2ab  2026-10-17 21:46  Functionality to add message to inbox queue + picks inbox and turns into stored report + log entry
  f0409b0  2026-10-17 18:58  Added base sql and load_base.py to load tables + data uploads
  52fa7f5  2026-10-17 16:20  Add migration runner, base schema and docker-compose stack
  0051c5f  2026-10-17 14:31  Adding intial backend configurations
  d9af414  2026-10-17 14:06  Adding folder structure for the project
```

### Prathamesh Thakur

```
  b1aa87c  2026-11-23 04:32  some ui changes
  554e9ad  2026-11-23 03:53  Ignore Docker install script and generated basemap tiles
  65e865e  2026-11-23 03:53  Fix duplicate plugins and server keys in vite.config.ts
  c5be812  2026-11-23 03:53  Redesign citizen report form and responder app UI
  fceeb2a  2026-11-22 12:16  added responder app
  cc892f7  2026-11-22 12:10  added responder app
  20f11f9  2026-11-22 12:01  added respnder app
  a4cf089  2026-11-17 23:08  Merge branch 'prathamesh'
  eef5cdc  2026-11-17 23:05  added dashboard
  8a62155  2026-10-28 21:06  added offersPannnel
  2be928d  2026-10-25 18:16  added incident pannel
  c4120d5  2026-10-25 18:14  Merge branch 'main' of https://github.com/RameezWahid/CS-699_Disaster-Relief into prathamesh
  cc0b115  2026-10-22 22:03  added Alerts pannel
  6305703  2026-10-22 22:00  added Alerts pannel
  a7e704b  2026-10-17 22:17  frontend added
  0677ab1  2026-10-17 22:06  added mapView
  a4e577e  2026-10-17 13:17  added login page and reportform
```
