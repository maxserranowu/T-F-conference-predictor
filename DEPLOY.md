# Deploying this as a live website (no terminal required)

## The mental model (important)

GitHub **stores** code. It does not **run** Python web apps — GitHub Pages only
serves static HTML/CSS/JS, and this app is Python (scraper + pandas +
scikit-learn + a Streamlit dashboard). So the setup is two pieces:

```
   GitHub  (holds the code)  ---->  a Python host  (runs it, gives you a URL)
```

The recommended Python host is **Streamlit Community Cloud** — it's free, it
connects straight to your GitHub repo, and everything below is done in the
browser. No terminal, ever.

---

## Step 1 — Put the code on GitHub (browser only)

1. Go to https://github.com/new and create a repo, e.g. `acc-ivy-predictor`
   (Public is required for the free Streamlit tier).
2. On the new repo page, click **“uploading an existing file”**.
3. Unzip `acc_ivy_predictor.zip` on your computer, then drag the **contents**
   of the `acc_ivy_predictor/` folder into the upload area (so `README.md`,
   `run.py`, `dashboard/`, `src/`, etc. sit at the repo root — not nested inside
   another folder). Commit.

That's it — the code (including the demo database at
`data/sample/predictor.db`) now lives on GitHub.

> The included `.gitignore` keeps a real `data/predictor.db` out of git by
> default but **allows** the committed demo DB. If you later want your real DB
> to deploy, see Step 3.

---

## Step 2 — Deploy the live site (browser only)

1. Go to https://share.streamlit.io and sign in with GitHub (authorize it).
2. Click **Create app → Deploy a public app from GitHub**.
3. Fill in:
   - **Repository:** `your-username/acc-ivy-predictor`
   - **Branch:** `main`
   - **Main file path:** `dashboard/app.py`
4. Click **Deploy**.

Streamlit installs `requirements.txt` automatically and builds the app. After a
minute you get a permanent public URL like
`https://your-username-acc-ivy-predictor.streamlit.app`. It launches straight
into the dashboard on the **demo data** (with the red “SYNTHETIC DEMO DATA”
banner), so you can confirm it works immediately. Every time you push to GitHub,
the site redeploys itself.

**This already satisfies “a standalone live site through GitHub.”** The only
remaining question is how to get *real* TFRRS data onto it — two no-terminal
options below.

---

## Step 3 — Put real data on the live site (pick one, both terminal-free)

The app never scrapes inside the web page (scraping is a slow, rate-limited crawl
that must not run on a page load). Instead you produce a `predictor.db` once and
the site reads it. Two ways to do that without a terminal:

### Option A — Upload a database through the site (simplest)
The sidebar has an **“Upload a predictor.db”** control. If you have a database
file, drop it in and the whole dashboard switches to it for your session.
(You still need to *produce* that file once — Option B does that for you.)

### Option B — Let GitHub build the database for you (fully hands-off)
The repo ships a GitHub Action at `.github/workflows/refresh-data.yml` that runs
the ingest **on GitHub's servers** and commits the resulting `data/predictor.db`
back to the repo — after which Streamlit auto-redeploys with real data.

1. In your repo, open the **Actions** tab and enable workflows if prompted.
2. Choose **“Refresh TFRRS data” → Run workflow**, set the conference and season
   range, and run it. It commits `data/predictor.db` when done.
3. Point the app at that DB by adding a setting on Streamlit Cloud:
   **Manage app → Settings → Secrets/Environment**, add
   `PREDICTOR_DB = "data/predictor.db"`, save. The app prefers that path over the
   demo DB. (Or just upload the committed file via the sidebar.)

> ⚠️ **Before enabling Option B:** read the “Scraping etiquette” section of the
> README and FloSports' Terms of Use. Scraping from shared CI IP ranges can be
> rate-limited or blocked, and the default 1-request-per-3-seconds throttle
> makes a full historical crawl take a while. The workflow is manual-trigger
> only and commented-schedule by design — you opt in.

---

## Alternative host: Hugging Face Spaces (also browser-only)

If you prefer Hugging Face: create a new **Space**, choose the **Streamlit** SDK,
and upload the same files through the web UI. Set the app file to
`dashboard/app.py`. Same result — a live URL, free tier, redeploys on change.

---

## What NOT to try

- **GitHub Pages / Netlify / Vercel static hosting** won't work for the full
  app — they can't run Python or Streamlit. (They *could* host a static export
  of charts, but you'd lose the interactive prediction/simulation/POV features.)
- **Don't run the scraper inside the deployed app.** Keep ingest in the GitHub
  Action (Option B) or run it once locally; the website only reads the database.

---

## Quick reference

| You want | Do this |
|---|---|
| A live demo site, no data setup | Steps 1–2. Done. |
| Load your own data into the live site | Sidebar upload (Option A) |
| Real data with zero terminal use | GitHub Action (Option B) + `PREDICTOR_DB` secret |
| A different free host | Hugging Face Spaces, Streamlit SDK |
