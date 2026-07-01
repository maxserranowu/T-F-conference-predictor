"""School-specific POV mode.

Answers, for a chosen school:
  * "Where am I losing the most points at conference, and to whom?"  -> scoring_leaks
  * "What does the data say about my event groups?"                   -> group_report
  * "What should my recruiting class focus on next year?"            -> recruiting_priorities

Every recommendation is tied to a scraped number and explains WHY it matters.
"""
from __future__ import annotations
import sqlite3
import pandas as pd

import features as F
from predictor import project_scores


def _latest_champ_year(con, conf_id, sport, gender):
    row = con.execute("""
        SELECT MAX(m.season_year) FROM meet m
        JOIN team_meet_score s ON s.meet_id=m.meet_id
        WHERE m.conf_id=? AND m.sport=? AND m.is_conf_champ=1 AND s.gender=?
    """, (conf_id, sport, gender)).fetchone()
    return row[0] if row else None


# --------------------------------------------------------------------------
def group_report(con, conf_id, sport, gender, school, season_year=None) -> pd.DataFrame:
    """Team's event-group strength index vs conference median, with a verdict."""
    yr = season_year or _latest_champ_year(con, conf_id, sport, gender)
    strength = F.event_group_strength(con, conf_id, sport, gender, yr)
    if strength.empty:
        return strength
    med = strength.groupby("event_group")["strength_index"].median().rename("conf_median")
    mine = strength[strength["team"] == school].set_index("event_group")
    out = mine.join(med, on="event_group")
    out["gap_vs_median"] = out["strength_index"] - out["conf_median"]
    out["verdict"] = out["gap_vs_median"].apply(
        lambda d: "STRENGTH" if d >= 8 else ("weakness" if d <= -8 else "average"))
    return out.reset_index()[["event_group", "strength_index", "conf_median",
                              "gap_vs_median", "n_athletes", "verdict"]]


# --------------------------------------------------------------------------
def scoring_leaks(con, conf_id, sport, gender, school, season_year=None) -> dict:
    """Events/groups where the school scores far below the champ/median, and the
    rivals who take those points. Returns {by_group, by_event, top_rivals}."""
    yr = season_year or _latest_champ_year(con, conf_id, sport, gender)
    g = F.group_points_by_champ(con, conf_id, sport, gender)
    g = g[g["season_year"] == yr]
    if g.empty:
        return {"note": f"no championship performance data for {yr}"}

    piv = g.pivot_table(index="event_group", columns="team",
                        values="points", aggfunc="sum", fill_value=0)
    champ_by_group = piv.max(axis=1)
    mine = piv[school] if school in piv.columns else piv.iloc[:, 0] * 0
    leaks = (pd.DataFrame({"my_points": mine,
                           "group_max": champ_by_group,
                           "leader": piv.idxmax(axis=1)})
             .assign(points_left_on_table=lambda d: d["group_max"] - d["my_points"])
             .sort_values("points_left_on_table", ascending=False)
             .reset_index())

    # event-level leaks
    ev = pd.read_sql_query("""
        SELECT p.event_norm, t.name AS team, SUM(p.points) pts
        FROM performance p JOIN meet m ON m.meet_id=p.meet_id
        JOIN team t ON t.team_id=p.team_id
        WHERE m.conf_id=? AND m.sport=? AND p.gender=? AND m.is_conf_champ=1
              AND m.season_year=?
        GROUP BY p.event_norm, t.name
    """, con, params=(conf_id, sport, gender, yr))
    ev_piv = ev.pivot_table(index="event_norm", columns="team",
                            values="pts", aggfunc="sum", fill_value=0)
    my_ev = ev_piv[school] if school in ev_piv.columns else ev_piv.iloc[:, 0] * 0
    ev_leaks = (pd.DataFrame({"my_points": my_ev,
                              "event_max": ev_piv.max(axis=1),
                              "leader": ev_piv.idxmax(axis=1)})
                .assign(gap=lambda d: d["event_max"] - d["my_points"])
                .sort_values("gap", ascending=False)
                .reset_index().head(10))

    # which rival beats me most across all events
    if school in piv.columns:
        rivals = {}
        for team in piv.columns:
            if team == school:
                continue
            rivals[team] = float((piv[team] - piv[school]).clip(lower=0).sum())
        top_rivals = (pd.Series(rivals).sort_values(ascending=False)
                      .head(3).reset_index())
        top_rivals.columns = ["rival", "net_points_ahead_of_me"]
    else:
        top_rivals = pd.DataFrame()

    return {"by_group": leaks, "by_event": ev_leaks, "top_rivals": top_rivals,
            "season_year": yr}


# --------------------------------------------------------------------------
def recruiting_priorities(con, conf_id, sport, gender, school,
                          season_year=None, graduating=None) -> pd.DataFrame:
    """Rank event groups by recruiting priority.

    Priority score blends:
      (a) points_left_on_table   — where the team currently bleeds points
      (b) group swing importance — how much that group decides THIS conference
      (c) graduation exposure    — points about to leave via departing seniors

    Higher score = recruit here first. Each row carries the 'why'.
    """
    yr = season_year or _latest_champ_year(con, conf_id, sport, gender)
    leaks = scoring_leaks(con, conf_id, sport, gender, school, yr)
    if "by_group" not in leaks:
        return pd.DataFrame()
    lg = leaks["by_group"].set_index("event_group")

    swing = F.deciding_groups(con, conf_id, sport, gender)
    swing = swing.set_index("event_group")["swing"] if not swing.empty else pd.Series(dtype=float)
    swing_norm = (swing / swing.max()) if len(swing) and swing.max() else swing

    # graduation exposure: senior points scored this year by group
    grad = pd.read_sql_query("""
        SELECT p.event_group, SUM(p.points) senior_points
        FROM performance p
        JOIN meet m ON m.meet_id=p.meet_id
        JOIN team t ON t.team_id=p.team_id
        JOIN roster_status rs ON rs.athlete_id=p.athlete_id
             AND rs.season_year=m.season_year
        WHERE t.name=? AND m.conf_id=? AND m.sport=? AND p.gender=?
              AND m.is_conf_champ=1 AND m.season_year=? AND rs.class_year='SR'
        GROUP BY p.event_group
    """, con, params=(school, conf_id, sport, gender, yr)).set_index("event_group")

    rows = []
    for grp in lg.index:
        leak = float(lg.loc[grp, "points_left_on_table"])
        sw = float(swing_norm.get(grp, 0.0)) if len(swing_norm) else 0.0
        exposure = float(grad["senior_points"].get(grp, 0.0)) if not grad.empty else 0.0
        # weighted priority
        score = 1.0 * leak + 6.0 * sw + 1.2 * exposure
        why = (f"{leak:.0f} pts below the group leader; "
               f"group swing importance {sw:.2f}; "
               f"{exposure:.0f} senior pts graduating.")
        rows.append({"event_group": grp, "priority_score": round(score, 1),
                     "points_left_on_table": round(leak, 1),
                     "swing_importance": round(sw, 2),
                     "senior_points_leaving": round(exposure, 1),
                     "why_it_matters": why,
                     "leader_taking_points": lg.loc[grp, "leader"]})
    return (pd.DataFrame(rows).sort_values("priority_score", ascending=False)
            .reset_index(drop=True))


# --------------------------------------------------------------------------
def full_pov_brief(con, conf_id, sport, gender, school, season_year=None) -> dict:
    """Everything a coach asks in one call."""
    yr = season_year or _latest_champ_year(con, conf_id, sport, gender)
    return {
        "school": school, "conference": conf_id, "sport": sport,
        "gender": gender, "season_year": yr,
        "group_report": group_report(con, conf_id, sport, gender, school, yr),
        "scoring_leaks": scoring_leaks(con, conf_id, sport, gender, school, yr),
        "recruiting_priorities": recruiting_priorities(con, conf_id, sport, gender, school, yr),
    }
