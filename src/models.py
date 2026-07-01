"""Dataclasses mirroring schema.sql. Lightweight (no pydantic dependency) so the
parser layer can build typed rows before they hit the DB."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Team:
    conf_id: str
    name: str
    gender: str            # 'm' | 'f'
    tfrrs_slug: Optional[str] = None
    source_url: Optional[str] = None


@dataclass
class Athlete:
    name: str
    team_name: str
    gender: str
    tfrrs_athlete_id: Optional[int] = None
    grad_year: Optional[int] = None
    source_url: Optional[str] = None


@dataclass
class Meet:
    tfrrs_meet_id: int
    name: str
    sport: str             # 'indoor' | 'outdoor' | 'xc'
    season_year: int
    conf_id: Optional[str] = None
    start_date: Optional[str] = None
    is_conf_champ: bool = False
    source_url: Optional[str] = None


@dataclass
class Performance:
    tfrrs_meet_id: int
    athlete_name: str
    team_name: str
    event_raw: str
    event_norm: str
    event_group: str
    gender: str
    season_year: int
    sport: str
    mark_raw: Optional[str] = None
    mark_seconds: Optional[float] = None
    mark_metric: Optional[float] = None
    is_field: bool = False
    wind: Optional[float] = None
    place: Optional[int] = None
    points: float = 0.0
    round: Optional[str] = None
    class_year: Optional[str] = None
    is_synthetic: bool = False
    source_url: Optional[str] = None


@dataclass
class TeamMeetScore:
    tfrrs_meet_id: int
    team_name: str
    gender: str
    place: Optional[int] = None
    points: Optional[float] = None
    is_synthetic: bool = False
    source_url: Optional[str] = None
