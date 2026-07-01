"""Scoring engine. Converts finishing places into team points for
track/field (points table) and cross country (low-score placement summing)."""
from __future__ import annotations
import yaml


class Scorer:
    def __init__(self, scoring_yaml_path: str):
        with open(scoring_yaml_path) as f:
            self.cfg = yaml.safe_load(f)
        self.tables = self.cfg["scoring_tables"]

    # --- track & field ----------------------------------------------------
    def points_for_place(self, place: int, table_name: str = "track_field_top8") -> float:
        table = self.tables[table_name]
        # yaml keys are ints already
        return float(table.get(place, 0.0))

    def score_event_placings(self, placings: list[tuple[str, int]],
                             table_name: str = "track_field_top8") -> dict[str, float]:
        """placings: [(team, place), ...] -> {team: points}."""
        out: dict[str, float] = {}
        for team, place in placings:
            out[team] = out.get(team, 0.0) + self.points_for_place(place, table_name)
        return out

    # --- cross country ----------------------------------------------------
    def score_xc(self, finish_order: list[str]) -> dict[str, int]:
        """finish_order: team name for each runner in finishing order (index 0 =
        1st). Standard rules: top 5 per team score, runners 6-7 displace, teams
        need >=5 finishers. Lowest total wins."""
        cfg = self.cfg["scoring_tables"]["xc_scoring"]
        n_score = cfg["scorers"]
        n_displace = cfg["displacers"]
        # assign running places only to eligible (full) teams
        counts: dict[str, int] = {}
        for t in finish_order:
            counts[t] = counts.get(t, 0) + 1
        eligible = {t for t, c in counts.items() if c >= n_score}

        place = 0
        team_runner_places: dict[str, list[int]] = {t: [] for t in eligible}
        for t in finish_order:
            if t not in eligible:
                continue
            place += 1
            if len(team_runner_places[t]) < (n_score + n_displace):
                team_runner_places[t].append(place)

        totals = {}
        for t, places in team_runner_places.items():
            totals[t] = sum(places[:n_score])
        return dict(sorted(totals.items(), key=lambda kv: kv[1]))
