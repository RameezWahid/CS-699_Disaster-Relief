# Gold set: writing and labelling guide

The gold set is the **test** the NLP pipeline is graded on (spec 4.2 and section 6, metrics 1–4).
It is never used for training or for tuning lexicons. Write it by hand, as people, without AI tools,
and without looking at `backend/scripts/extract.py` or `data/gazetteer/chronic_spots.csv` variants
(if you write to match the rules, the test measures nothing).

**Freeze: day 8.** After freezing, copy the file to `gold_v1.csv`, commit it, tag the commit
`gold-v1`, and never edit it again. Corrections go into a later `gold_v2.csv` with a note.

## Files

| File | What |
| --- | --- |
| `gold_template.csv` | Header for the main set. Copy to `gold_draft.csv` and fill in |
| `urgency_template.csv` | Header for the blind urgency ratings (metric 6) |

## Columns (one row per message)

| Column | Required | Allowed values | Meaning |
| --- | --- | --- | --- |
| `id` | yes | `G001`, `G002`, … | Unique, never reused |
| `author` | yes | Initials, e.g. `JG` | Who wrote the message |
| `lang` | yes | `en`, `hi`, `mr`, `hinglish`, `mixed` | Language the writer intended. Romanized Marathi is `mr` |
| `script` | yes | `latin`, `devanagari`, `mixed` | How it is typed |
| `text` | yes | 1–500 characters | The message exactly as a citizen would send it |
| `category` | yes | `rescue`, `medical`, `food_water`, `shelter`, `road_blocked`, `info_noise` | One label; see precedence below |
| `headcount` | no | Whole number | People needing help, only if the message states it. Blank otherwise |
| `needs` | no | `;`-separated from `boat`, `food`, `water`, `medicine`, `doctor`, `blanket`, `shelter`, `rescue` | Everything the message asks for |
| `vulnerability` | no | `;`-separated from `child`, `elderly`, `pregnant`, `disabled`, `oxygen`, `dialysis` | Every vulnerable person mentioned |
| `place_text` | no | Exact substring of `text` | The words that name the location. Blank if no place |
| `lat`, `lon` | if `place_text` | Decimal degrees inside Mumbai | True location of that place (from OpenStreetMap or a map app). For "near X", the point of X |
| `ward` | if `place_text` | One of the 24 BMC ward codes: `A B C D E F/N F/S G/N G/S H/E H/W K/E K/W L M/E M/W N P/N P/S R/C R/N R/S S T` | Ward containing `lat, lon` |
| `checked_by` | yes at freeze | Initials, not the author's | Second person who checked the labels |
| `notes` | no | Free text | E.g. "ambiguous: two Shivaji Chowks" |

### Label rules

- **Category precedence.** If a message has several needs, pick the most life-critical:
  `rescue` > `medical` > `shelter` > `food_water` > `road_blocked` > `info_noise`.
  - `rescue`: people trapped, stranded, in rising water, or asking to be evacuated.
  - `medical`: injury, illness, medicines, oxygen, dialysis, pregnancy, ambulance.
  - `shelter`: home flooded or unsafe, need a place to stay.
  - `food_water`: food or drinking water.
  - `road_blocked`: roads, subways, or tracks impassable, no people in danger.
  - `info_noise`: rain updates, rumours, jokes, prayers, forwards, questions, ads.
- **Headcount.** "5 log" → 5. "5-6 log" → 6 (the upper end). "family of four" → 4. "my family"
  with no number → blank. "2 bacche" with no other count → 2. Count people needing help, not
  buildings or vehicles.
- **Water.** Flood water ("paani bhar gaya") is not a `water` need; drinking water is.
- **Place.** `place_text` is copied character-for-character from `text`. If several places are
  named, use the one where help is needed. Roads mentioned only as "on the way" don't count.

## Mix to aim for (about 300 messages)

Write only in languages you are fluent in. Ask friends or family for the languages your team is
weaker in, and record them as authors by initials.

| Language | Script | Target |
| --- | --- | --- |
| English | latin | 60 |
| Hinglish | latin | 90 |
| Hindi | devanagari | 60 |
| Marathi | devanagari | 45 |
| Marathi | latin | 45 |

Across the whole set, roughly:

- Category: rescue 25%, medical 15%, food_water 15%, shelter 12%, road_blocked 13%, info_noise 20%.
- 50% or more state a headcount; vary digits (5), words (paanch, पाच), and Devanagari digits (५).
- 70% or more name a place. Use **any real Mumbai place**, not only famous flood spots: lanes,
  chawls, buildings, bus stops, temples, "near X", ward names, misspellings, and a few that are
  genuinely ambiguous (note them).
- 20% or more mention a vulnerable person.
- Realism: lengths from 3 to 60 words, typos, SMS shortcuts (pls, jldi), missing punctuation,
  emoji, forwarded-message style for some `info_noise`.

## Format illustration

This is the spec's own worked example, shown only to illustrate the format. **Do not include it or
paraphrase it in the gold set.**

```csv
id,author,lang,script,text,category,headcount,needs,vulnerability,place_text,lat,lon,ward,checked_by,notes
G000,XX,hinglish,latin,"Andheri subway pe paani bhar gaya hai, gaadi mein 5 log phase hain, ek baccha bhi hai, jaldi madad bhejo",rescue,5,rescue,child,Andheri subway,19.1204,72.8491,K/E,YY,
```

Text containing commas must be wrapped in double quotes (spreadsheet apps do this automatically
when you save as CSV, UTF-8).

## Urgency ratings (metric 6)

After the freeze, pick 50 ids at random. Two teammates, separately and without seeing the system's
output or each other's answers, rate each message 1 (low) to 5 (critical) in
`urgency_ratings.csv` (`id,rater,level`). The system's agreement is compared with theirs, and the
raters' agreement with each other is reported as the ceiling.
