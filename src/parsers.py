"""Parsers for TFRRS pages. Written against the live structure verified for
/leagues/62.html (ACC) and standard /results/{id}/... meet pages.

Robustness note: TFRRS markup drifts over the years. Parsers therefore key off
stable semantics (table headers like DATE/MEET/SPORT, event-header rows, PL/place
+ NAME + TEAM + TIME/MARK columns) rather than brittle CSS classes. Each parser
returns typed dataclasses from models.py and always fills source_url.
"""
from __future__ import annotations
import re
from typing import Iterable, Optional

from bs4 import BeautifulSoup

from event_groups import normalize_event, is_field_event
from models import Meet, Performance, TeamMeetScore

MEET_ID_RE = re.compile(r"/results/(?:xc/)?(\d+)/")
LIST_ID_RE = re.compile(r"/lists/(\d+)/")
YEAR_RE = re.compile(r"(20\d{2})")


# --------------------------------------------------------------------------
# League page: /leagues/{id}.html
# --------------------------------------------------------------------------
def parse_league_meets(html: str, source_url: str) -> list[dict]:
    """Return meet stubs {tfrrs_meet_id, name, sport, url, date_text} from the
    league page's MEET RESULTS table."""
    soup = BeautifulSoup(html, "lxml")
    out = []
    for a in soup.select("a[href*='/results/']"):
        href = a.get("href", "")
        m = MEET_ID_RE.search(href)
        if not m:
            continue
        name = a.get_text(strip=True)
        if not name:
            continue
        sport = "xc" if "/results/xc/" in href else "track"
        # find sibling sport cell if present
        row = a.find_parent("tr")
        date_text = None
        if row:
            cells = [c.get_text(strip=True) for c in row.find_all("td")]
            if cells:
                date_text = cells[0]
                joined = " ".join(cells).lower()
                if "cross country" in joined or "/results/xc/" in href:
                    sport = "xc"
        out.append({
            "tfrrs_meet_id": int(m.group(1)),
            "name": name,
            "sport": sport,
            "url": _abs(href),
            "date_text": date_text,
        })
    # dedupe by meet id
    seen, uniq = set(), []
    for r in out:
        if r["tfrrs_meet_id"] in seen:
            continue
        seen.add(r["tfrrs_meet_id"])
        uniq.append(r)
    return uniq


def parse_league_teams(html: str) -> list[dict]:
    """Return [{name, gender, slug}] from the league TEAMS table."""
    soup = BeautifulSoup(html, "lxml")
    teams = []
    for a in soup.select("a[href*='/teams/']"):
        href = a["href"]
        mt = re.search(r"/teams/(?:tf|xc)/([^./]+)\.html", href)
        if not mt:
            continue
        slug = mt.group(1)
        gender = "m" if "_m_" in slug else ("f" if "_f_" in slug else "?")
        teams.append({"name": a.get_text(strip=True), "gender": gender, "slug": slug})
    return teams


# --------------------------------------------------------------------------
# Meet results page: /results/{id}/{name}
# --------------------------------------------------------------------------
def _classify_sport(name: str, url: str) -> str:
    n = name.lower()
    if "/results/xc/" in url or "cross country" in n or " xc" in n:
        return "xc"
    if "indoor" in n:
        return "indoor"
    if "outdoor" in n:
        return "outdoor"
    return "outdoor"


def _season_year(name: str, date_text: Optional[str]) -> Optional[int]:
    for src in (date_text or "", name):
        m = YEAR_RE.search(src)
        if m:
            return int(m.group(1))
    return None


def parse_meet(html: str, stub: dict) -> tuple[Meet, list[Performance], list[TeamMeetScore]]:
    """Parse a full meet page into a Meet, per-athlete Performances, and (if the
    page shows a team-scores block) TeamMeetScores.

    The meet page is organized as a sequence of event blocks; each block has a
    header (gender + event name + round) followed by a result table whose columns
    are some subset of: PL | NAME | YEAR | TEAM | TIME/MARK | WIND | POINTS.
    """
    soup = BeautifulSoup(html, "lxml")
    url = stub["url"]
    name = stub["name"]
    sport = _classify_sport(name, url)
    year = _season_year(name, stub.get("date_text"))

    meet = Meet(
        tfrrs_meet_id=stub["tfrrs_meet_id"], name=name, sport=sport,
        season_year=year or 0, start_date=stub.get("date_text"),
        source_url=url,
    )

    perfs: list[Performance] = []
    scores: list[TeamMeetScore] = []

    # Team scores block(s): tables whose header includes 'PL' + 'TEAM' + 'PTS'
    for table in soup.find_all("table"):
        header = _header_cells(table)
        if _looks_like_team_scores(header):
            gender = _guess_gender_near(table)
            for tr in table.find_all("tr")[1:]:
                cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                if len(cells) < 2:
                    continue
                place = _to_int(cells[0])
                team = _clean_team(cells[1] if len(cells) > 1 else "")
                pts = _to_float(cells[-1])
                if team and pts is not None:
                    scores.append(TeamMeetScore(
                        tfrrs_meet_id=meet.tfrrs_meet_id, team_name=team,
                        gender=gender, place=place, points=pts, source_url=url))

    # Event result tables
    for table in soup.find_all("table"):
        header = _header_cells(table)
        if not _looks_like_event_results(header):
            continue
        event_ctx = _event_context_for(table)
        if not event_ctx:
            continue
        gender, event_raw, rnd = event_ctx
        event_norm = normalize_event(event_raw, sport)
        field = is_field_event(event_norm)
        group = _group_of(event_norm)
        colmap = _column_map(header)
        for tr in table.find_all("tr")[1:]:
            cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
            if len(cells) < 2:
                continue
            perf = _row_to_perf(cells, colmap, meet, gender, event_raw,
                                event_norm, group, field, url)
            if perf:
                perfs.append(perf)

    return meet, perfs, scores


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def parse_performance_list(html: str, source_url: str,
                           season_year: int, sport: str) -> list[dict]:
    """Parse a TFRRS performance / descending-order list page.

    URL shape: /lists/{id}/{Name}  (e.g. a conference outdoor descending list).
    These pages carry each athlete's SEASON BEST per event, already the "seed"
    marks used to project an upcoming championship. Structure mirrors meet
    results: repeated event blocks, each a table under an event header that
    names gender + event, with ATHLETE / YEAR / TEAM / TIME|MARK columns.

    Returns a list of season-best dicts (one per athlete-event), each stamped
    with source_url so every seed mark is TFRRS-traceable. No performances are
    invented: rows with no parseable mark are skipped.
    """
    soup = BeautifulSoup(html, "lxml")
    out: list[dict] = []
    seen: set[tuple] = set()

    for table in soup.find_all("table"):
        header = _header_cells(table)
        if not _looks_like_event_results(header):
            continue
        colmap = _column_map(header)
        if "name" not in colmap or "mark" not in colmap:
            continue

        ctx = _event_context_for(table)
        if ctx:
            gender, event_raw, _round = ctx
        else:
            gender, event_raw = _guess_gender_near(table), None
        if not event_raw:
            # list pages sometimes put the event only in a caption/first cell
            cap = table.find("caption")
            event_raw = cap.get_text(" ", strip=True) if cap else None
        if not event_raw:
            continue

        event_norm = normalize_event(event_raw)
        group = _group_of(event_norm)
        field = is_field_event(event_norm)

        body_rows = table.find_all("tr")[1:]
        for tr in body_rows:
            cells = [c.get_text(" ", strip=True)
                     for c in tr.find_all(["td", "th"])]
            if not cells:
                continue

            def g(key):
                i = colmap.get(key)
                return cells[i] if i is not None and i < len(cells) else None

            name = _clean_name(g("name"))
            team = _clean_team(g("team"))
            mark_raw = g("mark")
            if not name or not team or not mark_raw:
                continue

            secs = None if field else parse_time_to_seconds(mark_raw)
            metric = parse_mark_to_meters(mark_raw) if field else None
            if secs is None and metric is None:
                continue  # unparseable mark -> skip, never fabricate

            key = (name, team, event_norm)
            if key in seen:
                continue           # list is descending; first hit = best
            seen.add(key)

            out.append({
                "athlete": name, "team": team, "gender": gender,
                "season_year": season_year, "sport": sport,
                "event_raw": event_raw, "event_norm": event_norm,
                "event_group": group, "is_field": field,
                "mark_raw": mark_raw, "mark_seconds": secs,
                "mark_metric": metric, "class_year": _norm_class(g("year")),
                "source_url": source_url,
            })
    return out


def _abs(href: str) -> str:
    if href.startswith("http"):
        return href
    return "https://www.tfrrs.org" + href


def _header_cells(table) -> list[str]:
    head = table.find("tr")
    if not head:
        return []
    return [c.get_text(strip=True).upper() for c in head.find_all(["th", "td"])]


def _looks_like_team_scores(h: list[str]) -> bool:
    joined = " ".join(h)
    return ("TEAM" in joined and ("PTS" in joined or "POINTS" in joined)
            and "NAME" not in joined)


def _looks_like_event_results(h: list[str]) -> bool:
    joined = " ".join(h)
    has_name = "NAME" in joined or "ATHLETE" in joined
    has_mark = any(k in joined for k in ("TIME", "MARK", "DISTANCE", "HEIGHT", "POINTS"))
    return has_name and has_mark


def _column_map(h: list[str]) -> dict:
    m = {}
    for i, c in enumerate(h):
        if c in ("PL", "PLACE", "#"):
            m["place"] = i
        elif c in ("NAME", "ATHLETE"):
            m["name"] = i
        elif c in ("YEAR", "YR", "CL"):
            m["year"] = i
        elif c == "TEAM":
            m["team"] = i
        elif c in ("TIME", "MARK", "DISTANCE", "HEIGHT"):
            m["mark"] = i
        elif c == "WIND":
            m["wind"] = i
        elif c in ("PTS", "POINTS", "SCORE"):
            m["points"] = i
    return m


def _row_to_perf(cells, colmap, meet, gender, event_raw, event_norm,
                 group, field, url) -> Optional[Performance]:
    def g(key):
        i = colmap.get(key)
        return cells[i] if i is not None and i < len(cells) else None

    name = _clean_name(g("name"))
    team = _clean_team(g("team"))
    if not name or not team:
        return None
    mark_raw = g("mark")
    secs = None if field else parse_time_to_seconds(mark_raw)
    metric = parse_mark_to_meters(mark_raw) if field else None
    return Performance(
        tfrrs_meet_id=meet.tfrrs_meet_id, athlete_name=name, team_name=team,
        event_raw=event_raw, event_norm=event_norm, event_group=group,
        gender=gender, season_year=meet.season_year, sport=meet.sport,
        mark_raw=mark_raw, mark_seconds=secs, mark_metric=metric,
        is_field=field, wind=_to_float(g("wind")), place=_to_int(g("place")),
        points=_to_float(g("points")) or 0.0, round=None,
        class_year=_norm_class(g("year")), source_url=url,
    )


def _event_context_for(table) -> Optional[tuple[str, str, str]]:
    """Walk backwards from a result table to the nearest event header. TFRRS
    typically renders these as <h3>/<div> like 'Women 100 Meters' or
    'Men 5000 Meters Final'."""
    node = table
    for _ in range(6):
        node = node.find_previous(["h1", "h2", "h3", "h4", "div", "span"])
        if node is None:
            break
        txt = node.get_text(" ", strip=True)
        if not txt or len(txt) > 80:
            continue
        gender = None
        low = txt.lower()
        if low.startswith("women") or " women" in low:
            gender = "f"
        elif low.startswith("men") or " men" in low:
            gender = "m"
        if gender and any(ch.isalpha() for ch in txt):
            event = re.sub(r"^\s*(wo)?men'?s?\s*", "", txt, flags=re.I)
            rnd = "final"
            for r in ("prelim", "preliminaries", "semi", "final", "section"):
                if r in low:
                    rnd = r
            return gender, event.strip(), rnd
    return None


def _guess_gender_near(table) -> str:
    node = table.find_previous(string=re.compile(r"(men|women)", re.I))
    if node and "women" in node.lower():
        return "f"
    if node and "men" in node.lower():
        return "m"
    return "?"


def _group_of(event_norm: str) -> str:
    from event_groups import FIELD_EVENTS, MULTI_EVENTS
    sprint = {"60m", "100m", "200m", "400m", "60m Hurdles", "100m Hurdles",
              "110m Hurdles", "400m Hurdles", "4x100 Relay", "4x400 Relay"}
    dist = {"800m", "1000m", "1500m", "Mile", "3000m", "3000m Steeplechase",
            "5000m", "10000m", "Distance Medley Relay", "XC 6k", "XC 8k", "XC 10k"}
    jumps = {"High Jump", "Pole Vault", "Long Jump", "Triple Jump"}
    throws = {"Shot Put", "Weight Throw", "Discus", "Hammer", "Javelin"}
    if event_norm in sprint:
        return "sprints"
    if event_norm in dist:
        return "distance"
    if event_norm in jumps:
        return "jumps"
    if event_norm in throws:
        return "throws"
    if event_norm in MULTI_EVENTS:
        return "multis"
    return "other"


def _clean_name(s):
    if not s:
        return None
    s = re.sub(r"\s+", " ", s).strip()
    # TFRRS often "Last, First" -> "First Last"
    if "," in s:
        parts = [p.strip() for p in s.split(",")]
        if len(parts) == 2:
            s = f"{parts[1]} {parts[0]}"
    return s or None


def _clean_team(s):
    if not s:
        return None
    return re.sub(r"\s+", " ", s).strip() or None


def _norm_class(s):
    if not s:
        return None
    s = s.strip().upper()
    return {"FR": "FR", "SO": "SO", "JR": "JR", "SR": "SR",
            "1": "FR", "2": "SO", "3": "JR", "4": "SR",
            "GR": "GR", "FY": "FR"}.get(s, s[:2] if s else None)


def _to_int(s):
    if s is None:
        return None
    m = re.search(r"-?\d+", s)
    return int(m.group()) if m else None


def _to_float(s):
    if s is None:
        return None
    m = re.search(r"-?\d+(\.\d+)?", s.replace(",", ""))
    return float(m.group()) if m else None


def parse_time_to_seconds(s: Optional[str]) -> Optional[float]:
    """'13.42' -> 13.42 ; '1:48.55' -> 108.55 ; '2:03:14' handled too."""
    if not s:
        return None
    s = s.strip()
    m = re.match(r"^(\d+):(\d+):(\d+(?:\.\d+)?)$", s)      # h:m:s
    if m:
        h, mi, se = m.groups()
        return int(h) * 3600 + int(mi) * 60 + float(se)
    m = re.match(r"^(\d+):(\d+(?:\.\d+)?)$", s)            # m:s
    if m:
        mi, se = m.groups()
        return int(mi) * 60 + float(se)
    m = re.match(r"^(\d+(?:\.\d+)?)$", s)                  # seconds
    if m:
        return float(m.group(1))
    return None


def parse_mark_to_meters(s: Optional[str]) -> Optional[float]:
    """'7.65m' -> 7.65 ; '18.20m' -> 18.20 ; '6-10.75' (ft-in) -> meters ;
    multis points '6412' -> 6412 (kept as-is in mark_metric)."""
    if not s:
        return None
    s = s.strip().lower()
    m = re.match(r"^(\d+(?:\.\d+)?)\s*m$", s)
    if m:
        return float(m.group(1))
    m = re.match(r"^(\d+)-(\d+(?:\.\d+)?)$", s)            # feet-inches
    if m:
        ft, inch = int(m.group(1)), float(m.group(2))
        return round((ft * 12 + inch) * 0.0254, 3)
    m = re.match(r"^(\d{3,5})$", s)                        # multi points
    if m:
        return float(m.group(1))
    m = re.match(r"^(\d+(?:\.\d+)?)$", s)
    if m:
        return float(m.group(1))
    return None
