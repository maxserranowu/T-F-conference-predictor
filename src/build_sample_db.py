"""Build a SYNTHETIC demo database so the pipeline/dashboard run before scraping.

EVERY row is stamped is_synthetic=1. These are NOT real TFRRS performances and
must never be presented as such. Values are drawn from plausible ranges purely
so the models and dashboard have something to chew on. Deterministic (seeded).
"""
from __future__ import annotations
import os
import sqlite3
import numpy as np

from ingest import init_db

HERE = os.path.dirname(__file__)

ACC_TEAMS = ["Florida State", "Virginia Tech", "Clemson", "North Carolina",
             "NC State", "Notre Dame", "Virginia", "Duke", "Wake Forest",
             "Georgia Tech", "Louisville", "Pittsburgh"]

# (event_norm, group, is_field, base_mark, spread)  base_mark: secs or meters
EVENTS = [
    ("100m", "sprints", False, 10.35, 0.25),
    ("200m", "sprints", False, 20.9, 0.5),
    ("400m", "sprints", False, 46.5, 1.0),
    ("110m Hurdles", "sprints", False, 13.8, 0.4),
    ("800m", "distance", False, 149.0, 3.0),
    ("1500m", "distance", False, 228.0, 6.0),
    ("5000m", "distance", False, 830.0, 25.0),
    ("3000m Steeplechase", "distance", False, 540.0, 18.0),
    ("Long Jump", "jumps", True, 7.6, 0.4),
    ("High Jump", "jumps", True, 2.10, 0.08),
    ("Pole Vault", "jumps", True, 5.30, 0.25),
    ("Shot Put", "throws", True, 18.5, 1.2),
    ("Discus", "throws", True, 55.0, 5.0),
    ("Hammer", "throws", True, 62.0, 6.0),
    ("Decathlon", "multis", True, 7300, 350),
]

TABLE = {1: 10, 2: 8, 3: 6, 4: 5, 5: 4, 6: 3, 7: 2, 8: 1}
CLASSES = ["FR", "SO", "JR", "SR"]


def build(db_path: str, years=range(2013, 2027), seed=11):
    if os.path.exists(db_path):
        os.remove(db_path)
    rng = np.random.default_rng(seed)
    con = init_db(db_path)
    cur = con.cursor()

    cur.execute("INSERT INTO conference VALUES ('acc','ACC (SYNTHETIC)',62)")
    team_ids = {}
    for name in ACC_TEAMS:
        cur.execute("INSERT INTO team(conf_id,name,gender,tfrrs_slug,source_url) "
                    "VALUES ('acc',?,?,?,?)",
                    (name, "m", f"SYN_{name.replace(' ', '_')}", "SYNTHETIC"))
        team_ids[name] = cur.lastrowid

    # per-team latent quality (some programs simply stronger)
    quality = {t: rng.normal(0, 1) for t in ACC_TEAMS}
    # per-team, per-group tilt (identity: FSU sprints, distance schools, etc.)
    tilt = {t: {g: rng.normal(0, 0.6) for g in
                ["sprints", "distance", "jumps", "throws", "multis"]}
            for t in ACC_TEAMS}
    tilt["Florida State"]["sprints"] += 1.4
    tilt["Virginia Tech"]["throws"] += 1.2
    tilt["Notre Dame"]["distance"] += 1.3
    tilt["Virginia"]["distance"] += 1.0

    athlete_ids = {}

    def get_athlete(name, tid):
        key = (name, tid)
        if key in athlete_ids:
            return athlete_ids[key]
        cur.execute("INSERT INTO athlete(name,team_id,source_url) VALUES (?,?,?)",
                    (name, tid, "SYNTHETIC"))
        athlete_ids[key] = cur.lastrowid
        return cur.lastrowid

    aname = 0
    for year in years:
        cur.execute("""INSERT INTO meet(tfrrs_meet_id,name,sport,season_year,
                       start_date,is_conf_champ,conf_id,source_url)
                       VALUES (?,?,?,?,?,1,'acc','SYNTHETIC')""",
                    (900000 + year, f"{year} ACC Outdoor Championships (SYNTHETIC)",
                     "outdoor", year, f"{year}-05-15"))
        meet_id = cur.lastrowid

        for (ev, grp, is_field, base, spread) in EVENTS:
            entrants = []
            for t in ACC_TEAMS:
                n_ath = rng.integers(1, 3)  # 1-2 scorers per team
                strength = quality[t] + tilt[t][grp]
                for _ in range(n_ath):
                    aname += 1
                    name = f"Athlete_{aname}"
                    perf = rng.normal(strength, 1.0)
                    if is_field:
                        mark = base + perf * spread * 0.5
                    else:
                        mark = base - perf * spread * 0.5  # faster = better
                    entrants.append((t, name, mark))

            # rank -> place -> points
            entrants.sort(key=lambda x: (-x[2] if is_field else x[2]))
            for place, (t, name, mark) in enumerate(entrants, start=1):
                tid = team_ids[t]
                aid = get_athlete(name, tid)
                pts = TABLE.get(place, 0)
                cls = CLASSES[rng.integers(0, 4)]
                secs = None if is_field else float(mark)
                metric = float(mark) if is_field else None
                cur.execute("""INSERT INTO performance(meet_id,athlete_id,team_id,
                    event_raw,event_norm,event_group,gender,mark_raw,mark_seconds,
                    mark_metric,is_field,place,points,round,season_year_idx,
                    is_synthetic,source_url) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,'SYNTHETIC')""",
                    (meet_id, aid, tid, ev, ev, grp, "m",
                     f"{mark:.2f}", secs, metric, int(is_field), place, pts,
                     cls, year))
                # season best (seed) mirrors champ mark w/ small offset
                sb_secs = None if is_field else float(mark) * (1 + rng.normal(0, 0.004))
                sb_metric = float(mark) * (1 + rng.normal(0, 0.004)) if is_field else None
                cur.execute("""INSERT OR IGNORE INTO season_best(athlete_id,team_id,
                    season_year,sport,event_norm,event_group,gender,mark_seconds,
                    mark_metric,class_year,is_synthetic,source_url)
                    VALUES (?,?,?,?,?,?,?,?,?,?,1,'SYNTHETIC')""",
                    (aid, tid, year, "outdoor", ev, grp, "m", sb_secs, sb_metric, cls))
                cur.execute("""INSERT OR IGNORE INTO roster_status(athlete_id,
                    season_year,class_year,is_freshman,is_transfer,is_redshirt)
                    VALUES (?,?,?,?,0,0)""",
                    (aid, year, cls, int(cls == "FR")))

            # team scores for the meet (aggregate)
        # aggregate team scores from performances for this meet
        cur.execute("""INSERT INTO team_meet_score(meet_id,team_id,gender,place,points,
            is_synthetic,source_url)
            SELECT meet_id, team_id, 'm', NULL, SUM(points), 1, 'SYNTHETIC'
            FROM performance WHERE meet_id=? GROUP BY team_id""", (meet_id,))
        # assign places by points
        rows = cur.execute("""SELECT team_id, points FROM team_meet_score
            WHERE meet_id=? ORDER BY points DESC""", (meet_id,)).fetchall()
        for place, (tid, _) in enumerate(rows, start=1):
            cur.execute("UPDATE team_meet_score SET place=? WHERE meet_id=? AND team_id=?",
                        (place, meet_id, tid))

    con.commit()
    con.close()
    print(f"[SYNTHETIC] sample DB written to {db_path} "
          f"({len(list(years))} years, {len(ACC_TEAMS)} ACC teams, men, outdoor)")


if __name__ == "__main__":
    import sys
    build(sys.argv[1] if len(sys.argv) > 1 else "data/sample/predictor.db")
