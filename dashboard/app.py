"""Streamlit dashboard.

Run:  streamlit run dashboard/app.py -- --db data/sample/predictor.db

Tabs:
  1. Historical      — championship team scores over time (time-series)
  2. Event Groups    — which groups decide titles; team strength heat map
  3. Predictive      — projected scores, scoring ranges, win prob, swing events
  4. School POV      — weaknesses, scoring leaks, recruiting priorities
"""
import argparse
import os
import sqlite3
import sys

import pandas as pd
import plotly.express as px
import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import features as F
from predictor import project_scores, scoring_ranges
from simulate import simulate
from pov import full_pov_brief

SCORING = os.path.join(os.path.dirname(__file__), "..", "config", "scoring.yaml")
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_DB = os.path.join(REPO_ROOT, "data", "sample", "predictor.db")


def get_db_path():
    """Resolve the database in priority order so the app runs anywhere with no
    command-line args (needed on hosted platforms like Streamlit Cloud):
      1. a database uploaded through the sidebar this session
      2. the PREDICTOR_DB environment variable / secret
      3. a --db CLI arg (local use)
      4. the committed synthetic sample DB (always present -> app never crashes)
    """
    if st.session_state.get("uploaded_db_path"):
        return st.session_state["uploaded_db_path"]
    env = os.environ.get("PREDICTOR_DB")
    if env and os.path.exists(env):
        return env
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=None)
    args, _ = p.parse_known_args()
    if args.db and os.path.exists(args.db):
        return args.db
    return DEFAULT_DB


@st.cache_resource
def connect(db):
    return sqlite3.connect(db, check_same_thread=False)


def is_synthetic(con):
    row = con.execute("SELECT MAX(is_synthetic) FROM performance").fetchone()
    return bool(row and row[0])


st.set_page_config(page_title="ACC / Ivy Predictor", layout="wide")
db = get_db_path()
con = connect(db)

st.title("ACC / Ivy League Championship Predictor")
if is_synthetic(con):
    st.error("⚠️ SYNTHETIC DEMO DATA — not real TFRRS results. "
             "Run `python run.py ingest ...` to load real data.")

with st.sidebar:
    st.header("Data source")
    up = st.file_uploader(
        "Upload a predictor.db (optional)", type=["db", "sqlite", "sqlite3"],
        help="Load your own ingested database. Leave empty to use the "
             "built-in demo data.")
    if up is not None:
        import tempfile
        dst = os.path.join(tempfile.gettempdir(), "uploaded_predictor.db")
        with open(dst, "wb") as fh:
            fh.write(up.getbuffer())
        st.session_state["uploaded_db_path"] = dst
        st.success("Using uploaded database.")
    elif st.session_state.get("uploaded_db_path"):
        if st.button("Clear uploaded DB / use demo"):
            st.session_state.pop("uploaded_db_path", None)
            connect.clear()  # drop cached connection
            st.rerun()

    st.header("Filters")
    conf = st.selectbox("Conference", ["acc", "ivy"], index=0)
    sport = st.selectbox("Sport", ["outdoor", "indoor", "xc"], index=0)
    gender = st.selectbox("Gender", ["m", "f"], index=0)
    years = pd.read_sql_query(
        "SELECT DISTINCT season_year FROM meet WHERE conf_id=? AND sport=? "
        "AND is_conf_champ=1 ORDER BY season_year DESC", con, params=(conf, sport))
    year = st.selectbox("Season", years["season_year"].tolist()
                        if not years.empty else [2026])

tab_hist, tab_groups, tab_pred, tab_pov = st.tabs(
    ["📈 Historical", "🧩 Event Groups", "🔮 Predictive", "🎯 School POV"])

# ---------------------------------------------------------------- Historical
with tab_hist:
    st.subheader("Championship team scores over time")
    hist = F.champ_history(con, conf, sport, gender)
    if hist.empty:
        st.info("No championship history for this selection.")
    else:
        top = (hist.groupby("team")["points"].sum()
               .sort_values(ascending=False).head(8).index)
        fig = px.line(hist[hist["team"].isin(top)], x="season_year", y="points",
                      color="team", markers=True,
                      title="Team points at conference championship")
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Source: TFRRS championship meet pages (see source_url column).")
        st.dataframe(hist, use_container_width=True, height=260)

# ------------------------------------------------------------- Event Groups
with tab_groups:
    st.subheader("Which event groups decide titles?")
    dg = F.deciding_groups(con, conf, sport, gender)
    if not dg.empty:
        fig = px.bar(dg, x="event_group", y="swing",
                     title="Swing (std of group points across teams) — higher = more decisive")
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(dg, use_container_width=True)

    st.subheader(f"Team event-group strength index — {year}")
    strg = F.event_group_strength(con, conf, sport, gender, year)
    if not strg.empty:
        piv = strg.pivot(index="team", columns="event_group", values="strength_index")
        fig = px.imshow(piv, text_auto=".0f", aspect="auto",
                        color_continuous_scale="RdYlGn",
                        title="Strength index (0–100, conference-percentile based)")
        st.plotly_chart(fig, use_container_width=True)

# --------------------------------------------------------------- Predictive
with tab_pred:
    st.subheader(f"Projected team scores — {conf.upper()} {sport} {gender} {year}")
    totals, evproj = project_scores(con, conf, sport, gender, year, SCORING)
    if totals.empty:
        st.info("No season-best data to project this selection.")
    else:
        c1, c2 = st.columns([1, 1])
        with c1:
            fig = px.bar(totals, x="projected_points", y="team", orientation="h",
                         title="Projected points (mark-based seeding)")
            fig.update_yaxes(autorange="reversed")
            st.plotly_chart(fig, use_container_width=True)
        with c2:
            st.markdown("**Scoring ranges by group**")
            st.dataframe(scoring_ranges(evproj), use_container_width=True, height=380)

        st.markdown("---")
        n = st.slider("Monte Carlo trials", 1000, 20000, 5000, step=1000)
        if st.button("Run simulation"):
            with st.spinner("Simulating..."):
                res = simulate(con, conf, sport, gender, year, SCORING, n=n)
            if "error" in res:
                st.warning(res["error"])
            else:
                c3, c4 = st.columns([1, 1])
                with c3:
                    st.markdown("**Win probability**")
                    st.dataframe(res["win_prob"], use_container_width=True, height=380)
                with c4:
                    st.markdown("**Swing events** (correlation with title margin)")
                    st.dataframe(res["swing_events"].head(12),
                                 use_container_width=True, height=380)

# ---------------------------------------------------------------- School POV
with tab_pov:
    teams = pd.read_sql_query(
        "SELECT DISTINCT name FROM team WHERE conf_id=? AND gender=? ORDER BY name",
        con, params=(conf, gender))
    school = st.selectbox("School POV", teams["name"].tolist()
                          if not teams.empty else [])
    if school and st.button("Generate brief"):
        brief = full_pov_brief(con, conf, sport, gender, school, year)
        st.subheader(f"{school} — {brief['season_year']}")
        st.markdown("**Event-group report (vs conference median)**")
        st.dataframe(brief["group_report"], use_container_width=True)
        st.markdown("**Scoring leaks by group** — points left on the table & who takes them")
        st.dataframe(brief["scoring_leaks"]["by_group"], use_container_width=True)
        st.markdown("**Top rivals**")
        st.dataframe(brief["scoring_leaks"]["top_rivals"], use_container_width=True)
        st.markdown("**Recruiting priorities** (each row explains why)")
        st.dataframe(brief["recruiting_priorities"], use_container_width=True)
