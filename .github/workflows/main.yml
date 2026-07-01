"""Predictive modeling.

Two complementary approaches:

1. MARK-BASED SEEDING (primary, no training needed): rank each event field by
   current-season best marks, apply the scoring table to seeded places, sum per
   team. This is the coach's mental model and is fully explainable.

2. REGRESSION CALIBRATION (optional): a gradient-boosted / linear model learns
   the historical gap between *seeded* points and *actual* championship points
   per event group (accounting for prelims attrition, scratches, heat luck,
   improvement curves). Applied as a multiplicative correction to (1).

Outputs: projected team totals + a per-event scoring-range table.
"""
from __future__ import annotations
import sqlite3
import numpy as np
import pandas as pd

from scoring import Scorer


def _season_best_field(con, conf_id, sport, gender, season_year) -> pd.DataFrame:
    return pd.read_sql_query("""
        SELECT t.name AS team, a.name AS athlete, sb.event_norm, sb.event_group,
               sb.mark_seconds, sb.mark_metric, sb.class_year, sb.source_url
        FROM season_best sb
        JOIN team t ON t.team_id=sb.team_id
        JOIN athlete a ON a.athlete_id=sb.athlete_id
        WHERE t.conf_id=? AND sb.sport=? AND sb.gender=? AND sb.season_year=?
    """, con, params=(conf_id, sport, gender, season_year))


def _perf_seed_field(con, conf_id, sport, gender, season_year,
                     exclude_champ=False) -> pd.DataFrame:
    """Fallback 'season best' derived from ingested meet performances.

    Lets prediction and calibration work off meet data alone, before (or
    without) scraping /lists pages. For each athlete+event we keep the single
    best mark of the season (lowest time / highest field mark). Optionally drop
    the conference championship itself (used when building calibration training
    rows, so the seed can't peek at the meet it's trying to predict).
    """
    champ_filter = "AND m.is_conf_champ=0" if exclude_champ else ""
    df = pd.read_sql_query(f"""
        SELECT t.name AS team, a.name AS athlete, p.event_norm, p.event_group,
               p.mark_seconds, p.mark_metric, p.is_field, p.source_url,
               rs.class_year AS class_year
        FROM performance p
        JOIN team t ON t.team_id=p.team_id
        JOIN athlete a ON a.athlete_id=p.athlete_id
        JOIN meet m ON m.meet_id=p.meet_id
        LEFT JOIN roster_status rs
               ON rs.athlete_id=p.athlete_id AND rs.season_year=p.season_year_idx
        WHERE t.conf_id=? AND m.sport=? AND p.gender=? AND p.season_year_idx=?
              {champ_filter}
    """, con, params=(conf_id, sport, gender, season_year))
    if df.empty:
        return df
    # best mark per athlete+event
    field_mask = df["mark_metric"].notna()
    best_field = (df[field_mask].sort_values("mark_metric", ascending=False)
                  .groupby(["athlete", "event_norm"], as_index=False).first())
    best_time = (df[~field_mask].sort_values("mark_seconds", ascending=True)
                 .groupby(["athlete", "event_norm"], as_index=False).first())
    keep = ["team", "athlete", "event_norm", "event_group",
            "mark_seconds", "mark_metric", "class_year", "source_url"]
    return pd.concat([best_field[keep], best_time[keep]], ignore_index=True)


def _seed_field(con, conf_id, sport, gender, season_year,
                exclude_champ=False) -> pd.DataFrame:
    """Prefer scraped season bests (they ARE the pre-championship seed and can't
    peek at the meet); otherwise fall back to performance-derived seeds. When
    exclude_champ is set and we must use performances, the championship meet is
    dropped so a calibration seed never trains on its own target."""
    sb = _season_best_field(con, conf_id, sport, gender, season_year)
    if not sb.empty:
        return sb
    return _perf_seed_field(con, conf_id, sport, gender, season_year,
                            exclude_champ=exclude_champ)


def seed_event(field: pd.DataFrame) -> pd.DataFrame:
    """Rank one event's field into seed places (1 = best)."""
    is_field = field["mark_metric"].notna().any() and field["mark_seconds"].isna().all()
    col = "mark_metric" if is_field else "mark_seconds"
    field = field.sort_values(col, ascending=not is_field).reset_index(drop=True)
    field["seed_place"] = np.arange(1, len(field) + 1)
    return field


def _project_from_seed(seed_df: pd.DataFrame, scorer, table_name,
                       entries_per_team_per_event) -> pd.DataFrame:
    """Core seeding->scoring: rank each event's field, cap per-team entries,
    apply the scoring table. Returns the per-athlete event projection frame.
    Shared by project_scores (current season) and the calibration trainer
    (historical seasons), so both use identical seeding logic."""
    proj_rows = []
    for event, field in seed_df.groupby("event_norm"):
        is_field = field["mark_metric"].notna().any() and field["mark_seconds"].isna().all()
        col = "mark_metric" if is_field else "mark_seconds"
        field = field.sort_values(col, ascending=not is_field)
        field = field.groupby("team").head(entries_per_team_per_event)
        seeded = seed_event(field)
        for _, r in seeded.iterrows():
            pts = scorer.points_for_place(int(r["seed_place"]), table_name)
            proj_rows.append({
                "event_norm": event, "event_group": r["event_group"],
                "team": r["team"], "athlete": r["athlete"],
                "seed_place": int(r["seed_place"]), "proj_points": pts,
                "class_year": r.get("class_year"), "source_url": r.get("source_url"),
            })
    return pd.DataFrame(proj_rows)


def project_scores(con, conf_id, sport, gender, season_year,
                   scoring_yaml, table_name="track_field_top8",
                   entries_per_team_per_event=2) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (team_totals, event_projection).

    entries_per_team_per_event caps how many athletes a team can seed into a
    single event's scoring (most conferences limit declared entries; default 2
    is conservative — tune per conference rulebook)."""
    scorer = Scorer(scoring_yaml)
    sb = _seed_field(con, conf_id, sport, gender, season_year)
    if sb.empty:
        return pd.DataFrame(), pd.DataFrame()

    event_proj = _project_from_seed(sb, scorer, table_name,
                                    entries_per_team_per_event)
    if event_proj.empty:
        return pd.DataFrame(), pd.DataFrame()
    team_totals = (event_proj.groupby("team")["proj_points"].sum()
                   .reset_index().rename(columns={"proj_points": "projected_points"})
                   .sort_values("projected_points", ascending=False)
                   .reset_index(drop=True))
    team_totals["projected_place"] = np.arange(1, len(team_totals) + 1)
    return team_totals, event_proj


def scoring_ranges(event_proj: pd.DataFrame) -> pd.DataFrame:
    """Per (team, event_group) scoring floor/expected/ceiling.
    Floor = only seed-place-1 athletes hold; ceiling = everyone moves up ~1 place
    (a simple, transparent uncertainty band; simulate.py gives a rigorous one)."""
    if event_proj.empty:
        return event_proj
    rows = []
    for (team, grp), g in event_proj.groupby(["team", "event_group"]):
        expected = g["proj_points"].sum()
        floor = g[g["seed_place"] == 1]["proj_points"].sum()
        ceiling = expected * 1.35 + 2  # heuristic upside; replaced by sim band
        rows.append({"team": team, "event_group": grp,
                     "floor": round(floor, 1), "expected": round(expected, 1),
                     "ceiling": round(ceiling, 1)})
    return pd.DataFrame(rows).sort_values(["team", "event_group"])


# ==========================================================================
# REGRESSION CALIBRATION LAYER
# ==========================================================================
# Seeded points assume the meet plays out exactly on paper. Reality doesn't:
# prelim attrition, scratches, heat luck, tactical distance races, and freshman
# improvement all bend actual championship points away from the seed. This layer
# LEARNS that bend, per event group, from every historical championship in the
# database, and applies it as a transparent multiplicative correction.
#
# Training row  = (season, team, event_group)
#   x = seeded group points from that season's REGULAR-SEASON marks
#       (champ meet excluded, so the seed can't peek at its own target)
#   y = actual group points that team scored AT that season's championship
# Model         = per-group regression y ~ x  (Ridge; falls back to a robust
#                 ratio when a group has too few samples). Coefficients are
#                 exposed so a coach can see exactly how each group is adjusted.

from scoring import Scorer as _Scorer  # noqa: E402


def _seed_group_points(con, conf_id, sport, gender, season_year, scorer,
                       table_name, entries_per_team_per_event) -> pd.DataFrame:
    """Per (team, event_group) SEEDED points for one season, seeded from
    regular-season performances only (champ meet excluded)."""
    seed = _seed_field(con, conf_id, sport, gender, season_year,
                       exclude_champ=True)
    if seed.empty:
        return pd.DataFrame(columns=["team", "event_group", "seed_points"])
    ev = _project_from_seed(seed, scorer, table_name, entries_per_team_per_event)
    if ev.empty:
        return pd.DataFrame(columns=["team", "event_group", "seed_points"])
    return (ev.groupby(["team", "event_group"])["proj_points"].sum()
            .reset_index().rename(columns={"proj_points": "seed_points"}))


def _actual_champ_group_points(con, conf_id, sport, gender,
                               season_year) -> pd.DataFrame:
    """Per (team, event_group) ACTUAL points scored at that season's conference
    championship meet(s)."""
    df = pd.read_sql_query("""
        SELECT t.name AS team, p.event_group, SUM(p.points) AS actual_points
        FROM performance p
        JOIN team t ON t.team_id=p.team_id
        JOIN meet m ON m.meet_id=p.meet_id
        WHERE t.conf_id=? AND m.sport=? AND p.gender=?
              AND p.season_year_idx=? AND m.is_conf_champ=1
        GROUP BY t.name, p.event_group
    """, con, params=(conf_id, sport, gender, season_year))
    return df


def build_calibration_training(con, conf_id, sport, gender, scoring_yaml,
                               table_name="track_field_top8",
                               entries_per_team_per_event=2,
                               exclude_year=None) -> pd.DataFrame:
    """Stack (seed_points, actual_points) rows across every historical season
    that has a conference championship in the DB."""
    scorer = _Scorer(scoring_yaml)
    years = pd.read_sql_query("""
        SELECT DISTINCT p.season_year_idx AS y
        FROM performance p JOIN meet m ON m.meet_id=p.meet_id
        JOIN team t ON t.team_id=p.team_id
        WHERE t.conf_id=? AND m.sport=? AND p.gender=? AND m.is_conf_champ=1
        ORDER BY y
    """, con, params=(conf_id, sport, gender))["y"].tolist()

    frames = []
    for y in years:
        if exclude_year is not None and y == exclude_year:
            continue
        seed = _seed_group_points(con, conf_id, sport, gender, y, scorer,
                                  table_name, entries_per_team_per_event)
        actual = _actual_champ_group_points(con, conf_id, sport, gender, y)
        if seed.empty or actual.empty:
            continue
        m = seed.merge(actual, on=["team", "event_group"], how="outer").fillna(0.0)
        m["season_year"] = y
        frames.append(m)
    if not frames:
        return pd.DataFrame(columns=["team", "event_group", "seed_points",
                                     "actual_points", "season_year"])
    return pd.concat(frames, ignore_index=True)


class CalibrationModel:
    """Per-event-group regression mapping seeded points -> expected actual
    championship points. Explainable by design: one slope+intercept per group,
    with a ratio fallback when data is thin."""

    MIN_SAMPLES = 6  # below this, use a robust ratio instead of a fit

    def __init__(self):
        self.group_models = {}   # group -> ('linear', slope, intercept) | ('ratio', k)
        self.report_rows = []

    def fit(self, training: pd.DataFrame) -> "CalibrationModel":
        try:
            from sklearn.linear_model import Ridge
            have_sklearn = True
        except Exception:
            have_sklearn = False

        for grp, g in training.groupby("event_group"):
            x = g["seed_points"].to_numpy(dtype=float)
            y = g["actual_points"].to_numpy(dtype=float)
            n = len(g)
            if n >= self.MIN_SAMPLES and x.std() > 1e-6 and have_sklearn:
                model = Ridge(alpha=1.0, positive=True)
                model.fit(x.reshape(-1, 1), y)
                slope = float(model.coef_[0])
                intercept = float(model.intercept_)
                self.group_models[grp] = ("linear", slope, intercept)
                self.report_rows.append({
                    "event_group": grp, "method": "ridge", "n": n,
                    "slope": round(slope, 3), "intercept": round(intercept, 2)})
            else:
                # robust ratio: total actual / total seeded (guard /0)
                k = float(y.sum() / x.sum()) if x.sum() > 1e-6 else 1.0
                self.group_models[grp] = ("ratio", k)
                self.report_rows.append({
                    "event_group": grp, "method": "ratio", "n": n,
                    "slope": round(k, 3), "intercept": 0.0})
        return self

    def adjust(self, group: str, seed_points: float) -> float:
        m = self.group_models.get(group)
        if not m:
            return seed_points          # unseen group -> identity
        if m[0] == "linear":
            _, slope, intercept = m
            return max(0.0, slope * seed_points + intercept)
        return max(0.0, m[1] * seed_points)

    @property
    def report(self) -> pd.DataFrame:
        return pd.DataFrame(self.report_rows).sort_values("event_group") \
            if self.report_rows else pd.DataFrame()


def calibrated_projection(con, conf_id, sport, gender, season_year, scoring_yaml,
                          table_name="track_field_top8",
                          entries_per_team_per_event=2):
    """Full calibrated pipeline for one target season.

    Returns (team_totals, event_proj_calibrated, calib_report). team_totals has
    both raw 'projected_points' and calibrated 'calibrated_points'. Training
    excludes the target season so it never trains on the meet it predicts. If
    there's too little history to train, returns the raw projection with an
    empty report (and calibrated == projected)."""
    raw_totals, event_proj = project_scores(
        con, conf_id, sport, gender, season_year, scoring_yaml,
        table_name, entries_per_team_per_event)
    if event_proj.empty:
        return raw_totals, event_proj, pd.DataFrame()

    training = build_calibration_training(
        con, conf_id, sport, gender, scoring_yaml, table_name,
        entries_per_team_per_event, exclude_year=season_year)
    if training.empty:
        raw_totals = raw_totals.rename(columns={}).copy()
        raw_totals["calibrated_points"] = raw_totals["projected_points"]
        return raw_totals, event_proj, pd.DataFrame()

    model = CalibrationModel().fit(training)

    # collapse projection to (team, group) seed points, then calibrate
    grp = (event_proj.groupby(["team", "event_group"])["proj_points"].sum()
           .reset_index())
    grp["calibrated_points"] = [
        round(model.adjust(r.event_group, r.proj_points), 2)
        for r in grp.itertuples()]

    team_totals = (grp.groupby("team")
                   .agg(projected_points=("proj_points", "sum"),
                        calibrated_points=("calibrated_points", "sum"))
                   .reset_index()
                   .sort_values("calibrated_points", ascending=False)
                   .reset_index(drop=True))
    team_totals["projected_points"] = team_totals["projected_points"].round(1)
    team_totals["calibrated_points"] = team_totals["calibrated_points"].round(1)
    team_totals["calibrated_place"] = np.arange(1, len(team_totals) + 1)

    # attach calibrated group points back onto the event projection detail
    event_proj = event_proj.merge(
        grp[["team", "event_group", "calibrated_points"]],
        on=["team", "event_group"], how="left")
    return team_totals, event_proj, model.report
