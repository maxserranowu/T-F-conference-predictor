"""DB layer + scrape orchestration.

`init_db` builds the schema. `DB` wraps common upserts. `ingest_conference`
crawls a league page, filters meets to a season range, fetches each meet, parses
it, and writes performances + team scores. Championship meets are flagged using
the conference's champ keywords.
"""
from __future__ import annotations
import os
import sqlite3
from typing import Optional

import yaml

from tfrrs_client import TFRRSClient, BASE
from parsers import (parse_league_meets, parse_league_teams, parse_meet,
                     parse_performance_list, LIST_ID_RE)
from models import Meet, Performance, TeamMeetScore

HERE = os.path.dirname(__file__)
SCHEMA = os.path.join(HERE, "schema.sql")


def init_db(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    with open(SCHEMA) as f:
        con.executescript(f.read())
    con.commit()
    return con


class DB:
    def __init__(self, con: sqlite3.Connection):
        self.con = con

    def upsert_conference(self, conf_id, name, league_id):
        self.con.execute(
            "INSERT OR IGNORE INTO conference(conf_id,display_name,league_id) VALUES (?,?,?)",
            (conf_id, name, league_id))

    def team_id(self, name, gender, conf_id=None, slug=None, url=None) -> int:
        cur = self.con.execute(
            "SELECT team_id FROM team WHERE name=? AND gender=?", (name, gender))
        row = cur.fetchone()
        if row:
            return row[0]
        cur = self.con.execute(
            "INSERT INTO team(conf_id,name,gender,tfrrs_slug,source_url) VALUES (?,?,?,?,?)",
            (conf_id, name, gender, slug, url))
        return cur.lastrowid

    def athlete_id(self, name, team_id, gender, url=None) -> int:
        cur = self.con.execute(
            "SELECT athlete_id FROM athlete WHERE name=? AND team_id=?", (name, team_id))
        row = cur.fetchone()
        if row:
            return row[0]
        cur = self.con.execute(
            "INSERT INTO athlete(name,team_id,source_url) VALUES (?,?,?)",
            (name, team_id, url))
        return cur.lastrowid

    def insert_meet(self, m: Meet) -> int:
        self.con.execute(
            """INSERT OR IGNORE INTO meet(tfrrs_meet_id,name,sport,season_year,
                    start_date,is_conf_champ,conf_id,source_url)
               VALUES (?,?,?,?,?,?,?,?)""",
            (m.tfrrs_meet_id, m.name, m.sport, m.season_year, m.start_date,
             int(m.is_conf_champ), m.conf_id, m.source_url))
        return self.con.execute(
            "SELECT meet_id FROM meet WHERE tfrrs_meet_id=?",
            (m.tfrrs_meet_id,)).fetchone()[0]

    def insert_performance(self, meet_db_id, p: Performance, conf_id):
        tid = self.team_id(p.team_name, p.gender, conf_id, url=p.source_url)
        aid = self.athlete_id(p.athlete_name, tid, p.gender, p.source_url)
        self.con.execute(
            """INSERT INTO performance(meet_id,athlete_id,team_id,event_raw,event_norm,
                 event_group,gender,mark_raw,mark_seconds,mark_metric,is_field,wind,
                 place,points,round,season_year_idx,is_synthetic,source_url)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (meet_db_id, aid, tid, p.event_raw, p.event_norm, p.event_group,
             p.gender, p.mark_raw, p.mark_seconds, p.mark_metric, int(p.is_field),
             p.wind, p.place, p.points, p.round, p.season_year,
             int(p.is_synthetic), p.source_url))

    def insert_team_score(self, meet_db_id, s: TeamMeetScore, conf_id):
        tid = self.team_id(s.team_name, s.gender, conf_id, url=s.source_url)
        self.con.execute(
            """INSERT OR REPLACE INTO team_meet_score(meet_id,team_id,gender,place,
                    points,is_synthetic,source_url) VALUES (?,?,?,?,?,?,?)""",
            (meet_db_id, tid, s.gender, s.place, s.points, int(s.is_synthetic),
             s.source_url))

    def upsert_season_best(self, row: dict, conf_id) -> None:
        """Insert/replace one season-best seed mark from a TFRRS list page."""
        tid = self.team_id(row["team"], row["gender"], conf_id,
                           url=row["source_url"])
        aid = self.athlete_id(row["athlete"], tid, row["gender"],
                              row["source_url"])
        self.con.execute(
            """INSERT OR REPLACE INTO season_best(athlete_id,team_id,season_year,
                 sport,event_norm,event_group,gender,mark_seconds,mark_metric,
                 class_year,is_synthetic,source_url)
               VALUES (?,?,?,?,?,?,?,?,?,?,0,?)""",
            (aid, tid, row["season_year"], row["sport"], row["event_norm"],
             row["event_group"], row["gender"], row["mark_seconds"],
             row["mark_metric"], row["class_year"], row["source_url"]))
        # keep roster_status class_year in sync when the list exposes it
        if row.get("class_year"):
            self.con.execute(
                """INSERT OR IGNORE INTO roster_status(athlete_id,season_year,class_year)
                   VALUES (?,?,?)""",
                (aid, row["season_year"], row["class_year"]))


def discover_meets_from_team_pages(conf_id, db_path, cfg_path):
    """The league page shows only a rolling window of recent meets. To reach
    10-15 years of championships, crawl each configured team's TF and XC pages,
    which link every meet the team competed in. Returns a de-duped list of meet
    stubs {tfrrs_meet_id,name,sport,url}. Combine with ingest to backfill history.

    (TFRRS also exposes tf.tfrrs.org/archives.html for old performance lists; the
    per-team crawl is the most reliable path to full championship history.)
    """
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)
    conf = cfg["conferences"][conf_id]
    client = TFRRSClient(cfg)
    stubs, seen = [], set()
    for team in conf["teams"]:
        for slug_key in ("slug_m", "slug_f"):
            slug = team.get(slug_key)
            if not slug:
                continue
            for kind in ("tf", "xc"):
                url = f"{BASE}/teams/{kind}/{slug}.html"
                if not client.allowed(url):
                    continue
                try:
                    html = client.get(url, cacheable=False)
                except Exception:
                    continue
                for s in parse_league_meets(html, url):  # same MEET table shape
                    if s["tfrrs_meet_id"] in seen:
                        continue
                    seen.add(s["tfrrs_meet_id"])
                    stubs.append(s)
    return stubs


def _is_champ(name: str, keywords: list[str]) -> bool:
    low = name.lower()
    return any(k.lower() in low for k in keywords)


def _in_range(year: Optional[int], lo: int, hi: int) -> bool:
    return year is not None and lo <= year <= hi


def ingest_conference(conf_id: str, seasons: tuple[int, int], db_path: str,
                      cfg_path: str, only_champs: bool = False):
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)
    conf = cfg["conferences"][conf_id]
    client = TFRRSClient(cfg)
    con = init_db(db_path)
    db = DB(con)
    db.upsert_conference(conf_id, conf["display_name"], conf["league_id"])

    league_url = f"{BASE}/leagues/{conf['league_id']}.html"
    league_html = client.get(league_url, cacheable=False)  # current season changes
    teams = parse_league_teams(league_html)
    for t in teams:
        db.team_id(t["name"], t["gender"], conf_id, t["slug"], league_url)

    meet_stubs = parse_league_meets(league_html, league_url)
    lo, hi = seasons
    kw = conf["champ_meet_keywords"]

    for stub in meet_stubs:
        # cheap year filter from the stub date text before fetching
        yr = None
        if stub.get("date_text"):
            import re
            mm = re.search(r"(20\d{2})", stub["date_text"])
            yr = int(mm.group(1)) if mm else None
        champ = _is_champ(stub["name"], kw)
        if only_champs and not champ:
            continue

        html = client.get(stub["url"], cacheable=True)   # meets are immutable
        meet, perfs, scores = parse_meet(html, stub)
        meet.conf_id = conf_id
        meet.is_conf_champ = champ
        if not _in_range(meet.season_year, lo, hi):
            continue

        meet_db_id = db.insert_meet(meet)
        for p in perfs:
            db.insert_performance(meet_db_id, p, conf_id)
        for s in scores:
            db.insert_team_score(meet_db_id, s, conf_id)
        con.commit()
        print(f"  ingested {meet.season_year} {meet.sport:7s} "
              f"{'[CHAMP] ' if champ else ''}{meet.name[:50]} "
              f"({len(perfs)} perfs, {len(scores)} team scores)")

    con.commit()
    con.close()
    print(f"Done ingesting {conf_id} {lo}-{hi} -> {db_path}")


def _list_url(list_ref: str) -> str:
    """Accept a bare list id, a /lists/{id}/... path, or a full URL."""
    list_ref = str(list_ref).strip()
    if list_ref.startswith("http"):
        return list_ref
    if list_ref.startswith("/lists/"):
        return f"{BASE}{list_ref}"
    if list_ref.isdigit():
        # canonical name segment is cosmetic; TFRRS resolves on the id
        return f"{BASE}/lists/{list_ref}/List"
    m = LIST_ID_RE.search(list_ref)
    if m:
        return f"{BASE}/lists/{m.group(1)}/List"
    raise ValueError(f"Cannot interpret list reference: {list_ref!r}")


def ingest_lists(conf_id: str, list_refs, season_year: int, sport: str,
                 db_path: str, cfg_path: str):
    """Scrape TFRRS performance-list pages into the season_best table.

    list_refs: iterable of list ids / paths / URLs. Find them on TFRRS by
    opening the conference's descending-order list for the season+sport (e.g.
    the ACC outdoor list) and copying the /lists/{id}/... id. One list usually
    covers all events for one gender+sport+season; pass several to cover both
    genders or indoor+outdoor.

    These marks become the seeds that predictor.project_scores ranks. Nothing is
    fabricated: only rows with a parseable mark are stored, each with its
    source_url.
    """
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)
    conf = cfg["conferences"][conf_id]
    client = TFRRSClient(cfg)
    con = init_db(db_path)
    db = DB(con)
    db.upsert_conference(conf_id, conf["display_name"], conf["league_id"])

    total = 0
    for ref in list_refs:
        url = _list_url(ref)
        # season lists mutate until champs; refetch rather than trust cache
        html = client.get(url, cacheable=False)
        rows = parse_performance_list(html, url, season_year, sport)
        for r in rows:
            db.upsert_season_best(r, conf_id)
        con.commit()
        print(f"  list {url} -> {len(rows)} season-best marks")
        total += len(rows)

    con.commit()
    con.close()
    print(f"Done ingesting {total} season bests for {conf_id} "
          f"{season_year} {sport} -> {db_path}")
