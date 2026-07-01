#!/usr/bin/env python3
"""CLI for the ACC/Ivy predictor.

  python run.py build-sample [--db PATH]
  python run.py ingest   --conf acc --seasons 2011:2026 [--champs-only] [--db PATH]
  python run.py backfill --conf acc --seasons 2011:2026 [--db PATH]   # team-page crawl
  python run.py predict  --conf acc --season 2026 --gender m [--sport outdoor] [--db PATH]
  python run.py simulate --conf acc --season 2026 --gender m [--n 5000] [--db PATH]
  python run.py pov      --school "Virginia Tech" --conf acc [--gender m] [--db PATH]
"""
import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

CFG = os.path.join(os.path.dirname(__file__), "config", "conferences.yaml")
SCORING = os.path.join(os.path.dirname(__file__), "config", "scoring.yaml")


def _con(db):
    from ingest import init_db
    return init_db(db)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("build-sample"); p.add_argument("--db", default="data/sample/predictor.db")

    p = sub.add_parser("ingest")
    p.add_argument("--conf", required=True); p.add_argument("--seasons", default="2011:2026")
    p.add_argument("--champs-only", action="store_true"); p.add_argument("--db", default="data/predictor.db")

    p = sub.add_parser("backfill")
    p.add_argument("--conf", required=True); p.add_argument("--db", default="data/predictor.db")

    p = sub.add_parser("ingest-lists")
    p.add_argument("--conf", required=True)
    p.add_argument("--lists", required=True,
                   help="comma-separated TFRRS list ids / /lists/ paths / URLs")
    p.add_argument("--season", type=int, required=True)
    p.add_argument("--sport", default="outdoor")
    p.add_argument("--gender", default="m")  # informational; lists carry gender
    p.add_argument("--db", default="data/predictor.db")

    for name in ("predict", "simulate"):
        p = sub.add_parser(name)
        p.add_argument("--conf", required=True); p.add_argument("--season", type=int, required=True)
        p.add_argument("--gender", default="m"); p.add_argument("--sport", default="outdoor")
        p.add_argument("--n", type=int, default=5000); p.add_argument("--db", default="data/predictor.db")
        if name == "predict":
            p.add_argument("--calibrate", action="store_true",
                           help="apply the regression-calibration layer")

    p = sub.add_parser("pov")
    p.add_argument("--school", required=True); p.add_argument("--conf", required=True)
    p.add_argument("--gender", default="m"); p.add_argument("--sport", default="outdoor")
    p.add_argument("--season", type=int, default=None); p.add_argument("--db", default="data/predictor.db")

    a = ap.parse_args()

    if a.cmd == "build-sample":
        os.makedirs(os.path.dirname(a.db), exist_ok=True)
        from build_sample_db import build
        build(a.db)

    elif a.cmd == "ingest":
        from ingest import ingest_conference
        lo, hi = (int(x) for x in a.seasons.split(":"))
        os.makedirs(os.path.dirname(a.db) or ".", exist_ok=True)
        ingest_conference(a.conf, (lo, hi), a.db, CFG, only_champs=a.champs_only)

    elif a.cmd == "backfill":
        from ingest import discover_meets_from_team_pages
        stubs = discover_meets_from_team_pages(a.conf, a.db, CFG)
        print(f"discovered {len(stubs)} meet links via team pages; "
              f"re-run `ingest` to parse those in range.")

    elif a.cmd == "ingest-lists":
        from ingest import ingest_lists
        os.makedirs(os.path.dirname(a.db) or ".", exist_ok=True)
        refs = [x.strip() for x in a.lists.split(",") if x.strip()]
        ingest_lists(a.conf, refs, a.season, a.sport, a.db, CFG)

    elif a.cmd == "predict":
        con = _con(a.db)
        if getattr(a, "calibrate", False):
            from predictor import calibrated_projection, scoring_ranges
            totals, evproj, report = calibrated_projection(
                con, a.conf, a.sport, a.gender, a.season, SCORING)
            print("\n=== PROJECTED TEAM SCORES (raw vs calibrated) ===")
            print(totals.to_string(index=False))
            print("\n=== CALIBRATION MODEL (per event group) ===")
            if report.empty:
                print("  (not enough championship history to train; showing raw)")
            else:
                print(report.to_string(index=False))
        else:
            from predictor import project_scores, scoring_ranges
            totals, evproj = project_scores(con, a.conf, a.sport, a.gender, a.season, SCORING)
            print("\n=== PROJECTED TEAM SCORES ===")
            print(totals.to_string(index=False))
            print("\n=== SCORING RANGES BY GROUP (top rows) ===")
            print(scoring_ranges(evproj).head(20).to_string(index=False))

    elif a.cmd == "simulate":
        from simulate import simulate
        con = _con(a.db)
        res = simulate(con, a.conf, a.sport, a.gender, a.season, SCORING, n=a.n)
        if "error" in res:
            print(res["error"]); return
        print(f"\n=== WIN PROBABILITY ({a.n} sims) ===")
        print(res["win_prob"].to_string(index=False))
        print("\n=== SWING EVENTS (top 8) ===")
        print(res["swing_events"].head(8).to_string(index=False))

    elif a.cmd == "pov":
        from pov import full_pov_brief
        con = _con(a.db)
        brief = full_pov_brief(con, a.conf, a.sport, a.gender, a.school, a.season)
        print(f"\n=== POV BRIEF: {a.school} ({a.conf} {a.sport} {a.gender}, "
              f"{brief['season_year']}) ===")
        print("\n-- EVENT-GROUP REPORT --")
        print(brief["group_report"].to_string(index=False))
        print("\n-- SCORING LEAKS BY GROUP --")
        print(brief["scoring_leaks"]["by_group"].to_string(index=False))
        print("\n-- TOP RIVALS --")
        print(brief["scoring_leaks"]["top_rivals"].to_string(index=False))
        print("\n-- RECRUITING PRIORITIES --")
        print(brief["recruiting_priorities"].to_string(index=False))


if __name__ == "__main__":
    main()
