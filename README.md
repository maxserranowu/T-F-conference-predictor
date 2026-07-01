# ACC / Ivy League Conference Championship Predictor

An analytical agent for collegiate Track & Field and Cross Country that ingests
[TFRRS](https://www.tfrrs.org/) data for the **ACC** and **Ivy League**, builds
historical dashboards, runs predictive meet simulations, and answers
school-specific strategic questions.

> **Data honesty rule (hard constraint):** every number the agent reports must
> trace back to a scraped TFRRS row or to a model whose inputs are scraped TFRRS
> rows. Nothing is invented. The `data/sample/` database shipped with this repo
> is **synthetic** and exists only so the pipeline/dashboard run before you
> scrape. It is stamped `is_synthetic = 1` on every row and the dashboard shows a
> red "SYNTHETIC DATA" banner until you ingest real data.

---

## 1. What this repo contains

```
acc_ivy_predictor/
├── config/
│   ├── conferences.yaml     # ACC(62) & Ivy(55) league IDs, team slugs, membership-by-year
│   └── scoring.yaml         # scoring tables + event-group map
├── src/
│   ├── schema.sql           # relational schema (SQLite/Postgres compatible)
│   ├── models.py            # dataclasses mirroring the schema
│   ├── tfrrs_client.py      # polite HTTP client (cache, rate-limit, retry, robots)
│   ├── parsers.py           # BeautifulSoup parsers for league/team/meet pages
│   ├── ingest.py            # scrape orchestration -> DB
│   ├── event_groups.py      # event -> group mapping + group strength index
│   ├── scoring.py           # place -> points scoring engine
│   ├── features.py          # seed-vs-actual, improvement curves, depth charts, class impact
│   ├── predictor.py         # projected team scores + per-event scoring ranges
│   ├── simulate.py          # Monte Carlo meet sim, roster scenarios, swing events
│   ├── pov.py               # school POV: weaknesses, scoring leaks, recruiting priorities
│   └── build_sample_db.py   # generate the synthetic demo DB
├── dashboard/app.py         # Streamlit dashboard (historical + predictive + POV tabs)
├── examples/example_queries.md
└── run.py                   # CLI entry point
```

## 2. Quick start

```bash
pip install -r requirements.txt

# (a) Demo immediately on synthetic data — no scraping:
python run.py build-sample          # writes data/sample/predictor.db
streamlit run dashboard/app.py -- --db data/sample/predictor.db

# (b) Real data:
python run.py ingest --conf acc  --seasons 2011:2026 --db data/predictor.db
python run.py ingest --conf ivy  --seasons 2011:2026 --db data/predictor.db

# (c) Season-best seed marks for an upcoming championship (descending-order
#     "performance list" pages). Find the list id on TFRRS by opening the
#     conference's season list and copying the /lists/{id}/... number. One list
#     usually covers all events for one gender+sport+season; pass several
#     (comma-separated) to cover both genders or indoor+outdoor.
python run.py ingest-lists --conf acc --lists 3812,3813 --season 2026 --sport outdoor --db data/predictor.db

# (d) Predict, with or without the regression-calibration layer:
python run.py predict  --conf acc --season 2026 --gender m --db data/predictor.db
python run.py predict  --conf acc --season 2026 --gender m --calibrate --db data/predictor.db
python run.py simulate --conf acc --season 2026 --gender m --n 10000 --db data/predictor.db
python run.py pov --school "Virginia Tech" --conf acc --db data/predictor.db
```

### Seeds & calibration — how prediction gets its marks

`predict` needs a **seed mark** per athlete/event. It resolves them in this order:

1. **`season_best`** table, populated by `ingest-lists` from TFRRS list pages —
   the cleanest seed, exactly the descending-order marks a coach would use.
2. **Fallback:** if `season_best` is empty, the best regular-season mark per
   athlete/event is derived straight from ingested meet `performance` rows, so
   `predict` works immediately after `ingest` even before you scrape any lists.

The optional **`--calibrate`** flag then applies a learned correction. For every
past championship in the DB it compares *seeded* group points (from that
season's pre-champ marks) against *actual* points scored at that championship,
fits a per-event-group regression (Ridge, non-negative; robust ratio fallback
under 6 samples), and adjusts the current projection group-by-group. Training
always excludes the season being predicted, and the model prints one
slope+intercept per group so the adjustment is fully explainable.

## 3. Scraping etiquette (read before running `ingest`)

TFRRS is operated by DirectAthletics / FloSports. Before scraping at scale:

1. **Check `https://www.tfrrs.org/robots.txt`** — `tfrrs_client.py` fetches and
   honors it automatically; it will refuse disallowed paths.
2. **Rate-limit.** Default is 1 request / 3s with jitter + on-disk caching so a
   re-run costs zero requests. Do not lower this.
3. **Cache aggressively.** Historical meet pages never change; they are cached
   permanently. The current-season league page and `/lists/` season pages still
   mutate, so those are re-fetched each run (they pass `cacheable=False`).
4. **Identify yourself** via a real `User-Agent` + contact email in config.
5. Review FloSports' Terms of Use. This tool is for personal/coaching analysis;
   do not redistribute scraped raw data.

## 4. How each requirement maps to code

| Requirement | Module |
|---|---|
| Data acquisition & structuring | `tfrrs_client.py`, `parsers.py`, `ingest.py`, `schema.sql` |
| Athlete/event/team-score/YoY data | `schema.sql` tables + `features.py` |
| Event-group strength (sprint/distance/throw/jump/multi) | `event_groups.py` |
| Recruiting-class impact (frosh/transfer/redshirt) | `features.py::class_impact()` |
| Historical dashboard | `dashboard/app.py` (Historical tab) |
| Predictive modeling / scoring ranges | `predictor.py` |
| Season-best seed marks (TFRRS lists) | `parsers.py::parse_performance_list()`, `ingest.py::ingest_lists()` |
| Regression calibration (seed → actual) | `predictor.py::CalibrationModel`, `calibrated_projection()` |
| Swing events / roster scenarios | `simulate.py` |
| Improvement curves / depth charts | `features.py` |
| School POV mode | `pov.py` |
| Cite TFRRS pages | every table stores `source_url`; outputs print them |

## 5. Extending to other conferences

Add a block to `config/conferences.yaml` with the league's TFRRS `league_id`
and team slugs. No code changes required — every module reads the conference
registry, not hard-coded team lists.
