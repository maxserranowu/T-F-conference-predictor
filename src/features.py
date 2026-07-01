"""Feature engineering / analytics queries over the DB.

Everything here is a deterministic transform of scraped rows — no invented data.
Functions return pandas DataFrames for the dashboard and models to consume.
"""
from __future__ import annotations
import sqlite3
import pandas as pd

from event_groups import group_strength_index


def _df(con, sql, params=()):
    return pd.read_sql_query(sql, con, params=params)


# --------------------------------------------------------------------------
# Historical: championship team scores over time
# --------------------------------------------------------------------------
def champ_history(con, conf_id, sport, gender) -> pd.DataFrame:
    return _df(con, """
        SELECT m.season_year, t.name AS team, s.place, s.points, m.source_url
        FROM team_meet_score s
        JOIN meet m ON m.meet_id = s.meet_id
        JOIN team t ON t.team_id = s.team_id
        WHERE m.conf_id=? AND m.sport=? AND m.is_conf_champ=1 AND s.gender=?
        ORDER BY m.season_year, s.place
    """, (conf_id, sport, gender))


# --------------------------------------------------------------------------
# Which event groups decide titles: points by group at each championship
# --------------------------------------------------------------------------
def group_points_by_champ(con, conf_id, sport, gender) -> pd.DataFrame:
    df = _df(con, """
        SELECT m.season_year, t.name AS team, p.event_group,
               SUM(p.points) AS points
        FROM performance p
        JOIN meet m ON m.meet_id = p.meet_id
        JOIN team t ON t.team_id = p.team_id
        WHERE m.conf_id=? AND m.sport=? AND m.is_conf_champ=1 AND p.gender=?
        GROUP BY m.season_year, t.name, p.event_group
    """, (conf_id, sport, gender))
    return df


def deciding_groups(con, conf_id, sport, gender) -> pd.DataFrame:
    """For each championship year, correlation between a team's event-group
    points and its total — i.e. which groups most separate winners from the pack.
    Returns mean share of total points by group + a 'swing' score = std of group
    points across teams (high std => group creates the biggest gaps)."""
    g = group_points_by_champ(con, conf_id, sport, gender)
    if g.empty:
        return g
    totals = g.groupby(["season_year", "team"])["points"].sum().rename("team_total")
    g = g.merge(totals, on=["season_year", "team"])
    g["share"] = g["points"] / g["team_total"].replace(0, pd.NA)
    summary = (g.groupby("event_group")
                 .agg(mean_share=("share", "mean"),
                      swing=("points", "std"),
                      avg_points=("points", "mean"))
                 .reset_index()
                 .sort_values("swing", ascending=False))
    return summary


# --------------------------------------------------------------------------
# Over/under-performance vs seed
# --------------------------------------------------------------------------
def seed_vs_actual(con, conf_id, sport, gender, season_year) -> pd.DataFrame:
    """Compares each athlete's championship place to the place implied by their
    season-best seed. Positive delta = out-performed seed (gained places)."""
    perf = _df(con, """
        SELECT p.athlete_id, t.name AS team, p.event_norm, p.event_group,
               p.place AS actual_place, p.mark_seconds, p.mark_metric, p.is_field
        FROM performance p
        JOIN meet m ON m.meet_id=p.meet_id
        JOIN team t ON t.team_id=p.team_id
        WHERE m.conf_id=? AND m.sport=? AND m.is_conf_champ=1
              AND p.gender=? AND m.season_year=? AND p.place IS NOT NULL
    """, (conf_id, sport, gender, season_year))
    if perf.empty:
        return perf

    # implied seed place = rank of seed mark within event field
    def seed_rank(group):
        if group["is_field"].iloc[0]:
            group = group.sort_values("mark_metric", ascending=False)
        else:
            group = group.sort_values("mark_seconds", ascending=True)
        group["seed_place"] = range(1, len(group) + 1)
        return group

    perf = perf.groupby("event_norm", group_keys=False).apply(seed_rank)
    perf["place_delta"] = perf["seed_place"] - perf["actual_place"]  # + = gained
    return perf


def team_seed_performance(con, conf_id, sport, gender, season_year) -> pd.DataFrame:
    sv = seed_vs_actual(con, conf_id, sport, gender, season_year)
    if sv.empty:
        return sv
    return (sv.groupby("team")["place_delta"].sum()
              .reset_index().rename(columns={"place_delta": "places_gained_vs_seed"})
              .sort_values("places_gained_vs_seed", ascending=False))


# --------------------------------------------------------------------------
# Event-group strength index per team (conference-percentile based)
# --------------------------------------------------------------------------
def event_group_strength(con, conf_id, sport, gender, season_year) -> pd.DataFrame:
    """For the given season, express each athlete's season-best as a conference
    percentile within their event, then roll up to a per-team, per-group index."""
    sb = _df(con, """
        SELECT sb.athlete_id, t.name AS team, sb.event_norm, sb.event_group,
               sb.mark_seconds, sb.mark_metric
        FROM season_best sb
        JOIN team t ON t.team_id=sb.team_id
        WHERE t.conf_id=? AND sb.sport=? AND sb.gender=? AND sb.season_year=?
    """, (conf_id, sport, gender, season_year))
    if sb.empty:
        return sb

    def pctile(group):
        field = group["mark_metric"].notna().any() and group["mark_seconds"].isna().all()
        if field:
            vals = group["mark_metric"]
            group["pct"] = vals.rank(pct=True) * 100          # bigger = better
        else:
            vals = group["mark_seconds"]
            group["pct"] = (1 - vals.rank(pct=True)) * 100 + (100 / len(group))
            group["pct"] = group["pct"].clip(0, 100)
        return group

    sb = sb.groupby("event_norm", group_keys=False).apply(pctile)
    rows = []
    for (team, grp), g in sb.groupby(["team", "event_group"]):
        rows.append({"team": team, "event_group": grp,
                     "strength_index": group_strength_index(list(g["pct"])),
                     "n_athletes": len(g)})
    return pd.DataFrame(rows).sort_values(["team", "event_group"])


# --------------------------------------------------------------------------
# Depth charts: top-N athletes per team/event
# --------------------------------------------------------------------------
def depth_chart(con, conf_id, sport, gender, season_year, team=None, topn=3) -> pd.DataFrame:
    q = """
        SELECT t.name AS team, sb.event_norm, sb.event_group, a.name AS athlete,
               sb.class_year, sb.mark_seconds, sb.mark_metric
        FROM season_best sb
        JOIN team t ON t.team_id=sb.team_id
        JOIN athlete a ON a.athlete_id=sb.athlete_id
        WHERE t.conf_id=? AND sb.sport=? AND sb.gender=? AND sb.season_year=?
    """
    params = [conf_id, sport, gender, season_year]
    if team:
        q += " AND t.name=?"
        params.append(team)
    df = _df(con, q, tuple(params))
    if df.empty:
        return df

    def rank(group):
        field = group["mark_metric"].notna().any() and group["mark_seconds"].isna().all()
        group = group.sort_values("mark_metric" if field else "mark_seconds",
                                  ascending=not field)
        return group.head(topn)

    return df.groupby(["team", "event_norm"], group_keys=False).apply(rank)


# --------------------------------------------------------------------------
# Improvement curves: athlete season-best trajectory by class year
# --------------------------------------------------------------------------
def improvement_curves(con, conf_id, sport, gender, event_norm) -> pd.DataFrame:
    """Median year-over-year improvement by class transition (FR->SO, SO->JR,
    JR->SR). Powers 'expected improvement' priors in the predictor."""
    df = _df(con, """
        SELECT sb.athlete_id, sb.season_year, sb.class_year,
               sb.mark_seconds, sb.mark_metric, sb.event_norm
        FROM season_best sb
        JOIN team t ON t.team_id=sb.team_id
        WHERE t.conf_id=? AND sb.sport=? AND sb.gender=? AND sb.event_norm=?
        ORDER BY sb.athlete_id, sb.season_year
    """, (conf_id, sport, gender, event_norm))
    if df.empty:
        return df
    field = df["mark_metric"].notna().any() and df["mark_seconds"].isna().all()
    col = "mark_metric" if field else "mark_seconds"
    df = df.sort_values(["athlete_id", "season_year"])
    df["prev"] = df.groupby("athlete_id")[col].shift(1)
    df["prev_class"] = df.groupby("athlete_id")["class_year"].shift(1)
    # improvement: field = increase good, track = decrease good
    df["pct_change"] = (df[col] - df["prev"]) / df["prev"]
    if not field:
        df["pct_improve"] = -df["pct_change"]
    else:
        df["pct_improve"] = df["pct_change"]
    curve = (df.dropna(subset=["prev_class"])
               .groupby(["prev_class"])["pct_improve"]
               .median().reset_index().rename(columns={"pct_improve": "median_improve"}))
    return curve


# --------------------------------------------------------------------------
# Recruiting-class impact: points scored by frosh / transfers / redshirts
# --------------------------------------------------------------------------
def class_impact(con, conf_id, sport, gender, season_year) -> pd.DataFrame:
    df = _df(con, """
        SELECT t.name AS team, p.event_group,
               rs.is_freshman, rs.is_transfer, rs.is_redshirt,
               SUM(p.points) AS points
        FROM performance p
        JOIN meet m ON m.meet_id=p.meet_id
        JOIN team t ON t.team_id=p.team_id
        LEFT JOIN roster_status rs
             ON rs.athlete_id=p.athlete_id AND rs.season_year=m.season_year
        WHERE m.conf_id=? AND m.sport=? AND m.is_conf_champ=1
              AND p.gender=? AND m.season_year=?
        GROUP BY t.name, p.event_group, rs.is_freshman, rs.is_transfer, rs.is_redshirt
    """, (conf_id, sport, gender, season_year))
    return df
