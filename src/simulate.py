"""Monte Carlo championship simulation.

Each athlete's championship-day performance is drawn from a distribution centered
on their season best with event-appropriate noise (estimated from historical
seed-vs-actual scatter). We re-rank every event per trial, score it, and tally
team totals. Aggregating trials yields:

  * win probability per team
  * scoring-range distribution per team (rigorous replacement for the heuristic)
  * SWING EVENTS: events where the correlation between a team's event outcome and
    that team winning the meet is highest — i.e. the events that decide titles.
  * roster-scenario deltas: remove/add/redshirt an athlete and re-simulate.
"""
from __future__ import annotations
import sqlite3
import numpy as np
import pandas as pd

from scoring import Scorer

# Championship-day noise as a fraction of the mark. Sprints are tight; distance
# and field events swing more. These priors are overwritten by observed
# seed-vs-actual variance once real history is ingested (see calibrate_noise()).
DEFAULT_NOISE = {
    "sprints": 0.010, "distance": 0.018, "jumps": 0.025,
    "throws": 0.030, "multis": 0.030, "other": 0.020,
}


def calibrate_noise(con, conf_id, sport, gender) -> dict:
    """Estimate per-group noise from historical championship mark dispersion."""
    df = pd.read_sql_query("""
        SELECT p.event_group, p.event_norm, p.mark_seconds, p.mark_metric
        FROM performance p JOIN meet m ON m.meet_id=p.meet_id
        WHERE m.conf_id=? AND m.sport=? AND p.gender=? AND m.is_conf_champ=1
    """, con, params=(conf_id, sport, gender))
    if df.empty:
        return dict(DEFAULT_NOISE)
    out = dict(DEFAULT_NOISE)
    for grp, g in df.groupby("event_group"):
        col = "mark_metric" if g["mark_metric"].notna().any() else "mark_seconds"
        rel = g[col].std() / g[col].mean() if g[col].mean() else None
        if rel and 0 < rel < 0.15:
            out[grp] = float(min(max(rel * 0.4, 0.005), 0.05))
    return out


def _load_field(con, conf_id, sport, gender, season_year, entries_cap=2):
    sb = pd.read_sql_query("""
        SELECT t.name AS team, a.name AS athlete, sb.event_norm, sb.event_group,
               sb.mark_seconds, sb.mark_metric
        FROM season_best sb
        JOIN team t ON t.team_id=sb.team_id
        JOIN athlete a ON a.athlete_id=sb.athlete_id
        WHERE t.conf_id=? AND sb.sport=? AND sb.gender=? AND sb.season_year=?
    """, con, params=(conf_id, sport, gender, season_year))
    if sb.empty:
        return sb
    sb["is_field"] = sb["mark_metric"].notna() & sb["mark_seconds"].isna()
    sb["mark"] = np.where(sb["is_field"], sb["mark_metric"], sb["mark_seconds"])
    # cap entries per team per event by season best, without losing columns:
    # rank within (event, team); ascending for track (fast=better), desc for field
    sb["_sort"] = np.where(sb["is_field"], -sb["mark"], sb["mark"])
    sb["_rk"] = sb.groupby(["event_norm", "team"])["_sort"].rank(method="first")
    sb = sb[sb["_rk"] <= entries_cap].drop(columns=["_sort", "_rk"])
    return sb.reset_index(drop=True)


def simulate(con, conf_id, sport, gender, season_year, scoring_yaml,
             n=5000, table_name="track_field_top8", seed=7,
             drop_athletes=None, add_athletes=None):
    """Run n Monte Carlo trials. Returns dict with win_prob, score_dist,
    swing_events. `drop_athletes`/`add_athletes` enable roster scenarios."""
    rng = np.random.default_rng(seed)
    scorer = Scorer(scoring_yaml)
    sb = _load_field(con, conf_id, sport, gender, season_year)
    if sb.empty:
        return {"error": "no season-best data for this conf/season"}

    drop_athletes = set(drop_athletes or [])
    sb = sb[~sb["athlete"].isin(drop_athletes)].copy()
    if add_athletes:  # list of dicts: {team,athlete,event_norm,event_group,mark,is_field}
        sb = pd.concat([sb, pd.DataFrame(add_athletes)], ignore_index=True)

    noise = calibrate_noise(con, conf_id, sport, gender)
    events = list(sb.groupby("event_norm"))
    teams = sorted(sb["team"].unique())
    team_idx = {t: i for i, t in enumerate(teams)}

    totals = np.zeros((n, len(teams)))
    # per-event contribution to eventual winner, for swing detection
    event_team_points = {ev: np.zeros((n, len(teams))) for ev, _ in events}

    for ev, field in events:
        is_field = bool(field["is_field"].iloc[0])
        grp = field["event_group"].iloc[0]
        sd = noise.get(grp, 0.02)
        marks = field["mark"].to_numpy(dtype=float)
        tms = field["team"].map(team_idx).to_numpy()
        # simulate marks: multiplicative lognormal-ish noise
        draws = marks[None, :] * (1.0 + rng.normal(0, sd, size=(n, len(marks))))
        # rank within event per trial (ascending time / descending distance)
        order = np.argsort(draws if not is_field else -draws, axis=1)
        places = np.empty_like(order)
        rows = np.arange(n)[:, None]
        places[rows, order] = np.arange(1, len(marks) + 1)[None, :]
        # apply scoring table
        table = scorer.tables[table_name]
        pts_lookup = np.zeros(len(marks) + 2)
        for pl, pt in table.items():
            if 1 <= int(pl) <= len(marks):
                pts_lookup[int(pl)] = pt
        pts = pts_lookup[places]
        # accumulate into team totals
        for j in range(len(marks)):
            totals[:, tms[j]] += pts[:, j]
            event_team_points[ev][:, tms[j]] += pts[:, j]

    winners = np.argmax(totals, axis=1)
    win_prob = pd.DataFrame({
        "team": teams,
        "win_prob": [np.mean(winners == i) for i in range(len(teams))],
        "mean_points": totals.mean(axis=0),
        "p10": np.percentile(totals, 10, axis=0),
        "p90": np.percentile(totals, 90, axis=0),
    }).sort_values("win_prob", ascending=False).reset_index(drop=True)

    # swing events: for the top-2 contenders, which events most correlate with
    # the meet margin between them
    swing = _swing_events(totals, event_team_points, teams, win_prob)

    return {"win_prob": win_prob, "swing_events": swing,
            "score_matrix": totals, "teams": teams}


def _swing_events(totals, event_team_points, teams, win_prob):
    if len(teams) < 2:
        return pd.DataFrame()
    a = teams.index(win_prob.iloc[0]["team"])
    b = teams.index(win_prob.iloc[1]["team"])
    margin = totals[:, a] - totals[:, b]           # meet margin between top 2
    rows = []
    for ev, mat in event_team_points.items():
        ev_margin = mat[:, a] - mat[:, b]
        if ev_margin.std() == 0 or margin.std() == 0:
            corr = 0.0
        else:
            corr = float(np.corrcoef(ev_margin, margin)[0, 1])
        rows.append({"event_norm": ev,
                     "swing_corr": round(corr, 3),
                     "mean_margin_contribution": round(ev_margin.mean(), 2),
                     "margin_volatility": round(ev_margin.std(), 2)})
    return (pd.DataFrame(rows)
            .sort_values("swing_corr", ascending=False)
            .reset_index(drop=True))


def roster_scenario(con, conf_id, sport, gender, season_year, scoring_yaml,
                    drop=None, add=None, n=5000):
    """Convenience: baseline vs scenario win-prob delta."""
    base = simulate(con, conf_id, sport, gender, season_year, scoring_yaml, n=n)
    scen = simulate(con, conf_id, sport, gender, season_year, scoring_yaml,
                    n=n, drop_athletes=drop, add_athletes=add)
    if "error" in base or "error" in scen:
        return base
    merged = base["win_prob"][["team", "win_prob"]].merge(
        scen["win_prob"][["team", "win_prob"]], on="team",
        suffixes=("_base", "_scenario"))
    merged["delta"] = merged["win_prob_scenario"] - merged["win_prob_base"]
    return merged.sort_values("delta", ascending=False).reset_index(drop=True)
