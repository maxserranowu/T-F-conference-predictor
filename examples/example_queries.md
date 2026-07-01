# Example queries & outputs

All CLI examples run against the synthetic demo DB. Swap `--db data/predictor.db`
after you ingest real TFRRS data. Outputs below are from the **synthetic** data —
they illustrate shape/behavior, not real ACC results.

---

## 1. "What happened at conference over the last N years?"

```bash
python run.py pov --school "Florida State" --conf acc --gender m --db data/sample/predictor.db
```
Dashboard: **Historical** tab → team-points time-series; **Event Groups** tab →
which groups swing titles.

## 2. "Predict this year's meet from current marks."

```bash
python run.py predict --conf acc --season 2026 --gender m --db data/sample/predictor.db
```
Example (synthetic):
```
=== PROJECTED TEAM SCORES ===
          team  projected_points  projected_place
 Virginia Tech             119.0                1
    Louisville             102.0                2
   Wake Forest              80.0                3
 ...
```

## 3. "What are the scoring ranges and win probabilities?"

```bash
python run.py simulate --conf acc --season 2026 --gender m --n 8000 --db data/sample/predictor.db
```
Example (synthetic):
```
=== WIN PROBABILITY (8000 sims) ===
          team  win_prob  mean_points  p10   p90
 Virginia Tech    0.6559       100.04 85.0 115.0
    Louisville    0.2871        90.91 77.0 105.0
 ...
=== SWING EVENTS (top) ===
        event_norm  swing_corr  mean_margin_contribution
3000m Steeplechase       0.358                    -4.47
         Long Jump       0.324                     3.15
              800m       0.320                    -3.48
```
Read: the steeplechase, long jump, and 800m most strongly correlate with the
VT–Louisville title margin — those are the events to watch.

## 4. School POV: "Where am I losing points, and to whom?"

```bash
python run.py pov --school "Florida State" --conf acc --gender m --db data/sample/predictor.db
```
Example (synthetic):
```
-- EVENT-GROUP REPORT --
event_group  strength_index  conf_median  gap_vs_median  verdict
   distance            62.1        75.80         -13.70  weakness
     sprints            94.7        66.70          28.00  STRENGTH

-- SCORING LEAKS BY GROUP --
event_group  my_points  group_max        leader  points_left_on_table
      jumps        7.0       35.0 Virginia Tech                  28.0
   distance        6.0       29.0    Louisville                  23.0

-- RECRUITING PRIORITIES --
event_group  priority_score  why_it_matters
   distance            35.5  23 pts below leader; swing importance 0.89; 6 senior pts graduating.
      jumps            33.2  28 pts below leader; swing importance 0.87; 0 senior pts graduating.
```

## 5. Roster scenario: "What if my star transfers out?"

```python
from simulate import roster_scenario
from ingest import init_db
con = init_db("data/sample/predictor.db")
delta = roster_scenario(con, "acc", "outdoor", "m", 2026,
                        "config/scoring.yaml", drop=["Athlete_3565"], n=8000)
print(delta)   # win-prob change per team when that athlete is removed
```

## 6. Load season-best seed marks from a TFRRS performance list

```bash
# Grab the /lists/{id}/... id from the conference's descending-order list on
# TFRRS, then ingest one or more (comma-separated). Gender comes from the page.
python run.py ingest-lists --conf acc --lists 3812,3813 --season 2026 --sport outdoor
```

Every stored season best carries its `source_url`, and duplicate rows for the
same athlete/event collapse to the fastest time / farthest mark. Once loaded,
`predict` seeds from these automatically.

## 7. Calibrated projection (learned seed → actual correction)

```bash
python run.py predict --conf acc --season 2026 --gender m --calibrate
```

```python
from predictor import calibrated_projection
from ingest import init_db
con = init_db("data/sample/predictor.db")
totals, event_proj, report = calibrated_projection(
    con, "acc", "outdoor", "m", 2026, "config/scoring.yaml")
print(totals)   # projected_points (raw) vs calibrated_points
print(report)   # per event group: method, n, slope, intercept
```

Example calibration report on the synthetic DB (slopes near 1.0 because the
sample data is internally consistent; real TFRRS history will bend groups like
distance and multis more, where prelim attrition and tactics diverge from seed):

```
event_group method   n  slope  intercept
   distance  ridge 156  0.988       0.16
      jumps  ridge 156  0.993       0.07
     multis  ridge 156  0.954       0.15
    sprints  ridge 156  0.961       0.51
     throws  ridge 156  1.001      -0.01
```

## 8. Natural-language POV prompts the agent maps to functions

| User says | Function |
|---|---|
| "I'm from Cornell — what should we recruit next year?" | `pov.recruiting_priorities` |
| "Where does Yale leak the most points at Heps?" | `pov.scoring_leaks` |
| "How strong are Princeton's throws vs the Ivy?" | `pov.group_report` |
| "Simulate ACC if NC State's 400 crew redshirts" | `simulate.roster_scenario` |
| "Which events decided the last 10 ACC titles?" | `features.deciding_groups` |
