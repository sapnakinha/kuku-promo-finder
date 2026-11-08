"""
app.py - a simple viewer for the promo finder's results.

Run it from the project folder:
    streamlit run app.py

It reads the files a run already produced in outputs/<run name>/ and shows:
  - every episode and the promos the model picked for it
  - the overall ranking and the week-1 posting plan
  - what the rule checker rejected, and why
No AI calls happen here - it only displays results.
"""

import csv
import io
import json
import os
import subprocess
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path

import streamlit as st

import config
from parse_script import fmt_time, load_show

OUTPUTS = Path("outputs")          # your own runs (git-ignored, only on your computer)
PRIVATE = Path("private")          # encrypted (.locked) shows - need a password to open
PUBLIC = Path("public")            # text-free copies of real runs (export_public.py)
PUBLIC_SRC = "Kuku show: results"
LOCKED_SRC = "Kuku show: full text (password)"
HIDDEN = "Dialogue hidden to protect Kuku's content. Unlock with the password to read it."
DATA = Path("data")

st.set_page_config(page_title="Promo Finder", layout="wide")

st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,500;8..60,600&display=swap');
  #MainMenu, footer, header [data-testid="stToolbar"], .stDeployButton {visibility: hidden;}
  header[data-testid="stHeader"] {background: transparent;}
  .block-container {padding-top: 4.5rem; max-width: 1080px;}
  [data-testid="stSidebar"] {background: #EFEDE8; border-right: 1px solid #E2DFD8;}

  .eyebrow {font-size: 0.72rem; letter-spacing: 2px; text-transform: uppercase;
            color: #3E6A7A; font-weight: 600; margin-bottom: 4px;}
  .show-title {font-family: 'Source Serif 4', Georgia, serif; font-size: 2.45rem; font-weight: 600;
               color: #1E2A36; line-height: 1.12; letter-spacing: -0.3px; margin: 0;}

  .timeline {margin: 4px 0 10px 0;}
  .tl-row {display: flex; align-items: center; gap: 10px; margin-top: 4px;}
  .tl-ep {font-size: 0.72rem; color: #8A929A; width: 34px; flex: none;}
  .tl-bar {position: relative; flex: 1; height: 6px; background: #EFEDE8; border-radius: 3px;}
  .tl-seg {position: absolute; top: 0; height: 6px; border-radius: 3px; background: #6E8F9B;}
  .tl-end {font-size: 0.72rem; color: #8A929A; width: 40px; text-align: right; flex: none;}

  .spot-head {font-size: 0.72rem; letter-spacing: 1.6px; text-transform: uppercase; color: #8A929A;
              margin: 4px 0 10px 0;}
  .spot {display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; margin-bottom: 30px;}
  .spot-card {background: #FFFFFF; border: 1px solid #E6E3DC; border-radius: 10px; padding: 16px 18px;
              transition: box-shadow .18s ease, transform .18s ease;}
  .spot-card:hover {box-shadow: 0 6px 18px rgba(30,42,54,0.08); transform: translateY(-1px);}

  .spot-rank {font-family: 'Source Serif 4', Georgia, serif; font-size: 2rem; font-weight: 500;
              color: #3E6A7A; line-height: 1;}
  .spot-hook {font-family: 'Source Serif 4', Georgia, serif; font-size: 1.05rem; color: #1E2A36;
              line-height: 1.35; margin: 10px 0 12px 0;}
  .spot-meta {font-size: 0.78rem; color: #6E7781;}

  .site-footer {margin-top: 48px; padding-top: 16px; border-top: 1px solid #E2DFD8;
                display: flex; justify-content: space-between; font-size: 0.8rem; color: #8A929A;}
  .site-footer a {color: #3E6A7A; text-decoration: none;}
  .meta {color: #6E7781; font-size: 0.88rem; margin: 6px 0 22px 0;
         padding-bottom: 18px; border-bottom: 1px solid #E2DFD8;}

  .stats {display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; margin-bottom: 32px;}
  .stat {background: #FFFFFF; border: 1px solid #E6E3DC; border-radius: 10px; padding: 16px 18px;
         min-width: 0;}
  .stat .n {font-size: 1.7rem; font-weight: 600; color: #1E2A36; line-height: 1.15;}
  .stat .l {font-size: 0.8rem; color: #6E7781; margin-top: 4px;}

  .stTabs [data-baseweb="tab-list"] {gap: 22px; border-bottom: 1px solid #E2DFD8;}
  .stTabs [data-baseweb="tab"] {padding: 8px 2px; color: #6E7781;}
  .stTabs [aria-selected="true"] {color: #1E2A36 !important; font-weight: 600;}
  .stTabs [data-baseweb="tab-highlight"] {background-color: #3E6A7A;}

  div[class*="st-key-card"] {background: #FFFFFF; border: 1px solid #E6E3DC; border-radius: 10px;
        padding: 18px 22px 16px 22px; margin-bottom: 16px; gap: 0.6rem;
        transition: box-shadow .18s ease, transform .18s ease;}
  div[class*="st-key-card"]:hover {box-shadow: 0 6px 18px rgba(30,42,54,0.08); transform: translateY(-1px);}
  div[class*="st-key-card"].rejected {opacity: .75;}

  .row {display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; row-gap: 6px;}
  .tag {display: inline-block; padding: 2px 9px; border-radius: 20px; font-size: 0.74rem;
        background: #F1EFEA; color: #4A5560; border: 1px solid #E6E3DC;}
  .tag.rank {background: #1E2A36; color: #fff; border-color: #1E2A36; font-weight: 600;}
  .tag.rej {background: #F6ECE9; color: #8A4F43; border-color: #EBD7D1;}
  .tag.dup {background: #F3F3F3; color: #888;}
  .times {font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 0.84rem; color: #4A5560;
          margin-left: auto; white-space: nowrap;}
  .hc {display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 24px; margin: 8px 0 10px 0;}
  .label {color: #8A929A; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 1.2px;
          margin-bottom: 2px;}
  .val {color: #1E2A36; font-size: 0.97rem; line-height: 1.4;}
  .why {color: #4A5560; font-size: 0.88rem; margin: 4px 0 8px 0;}
  .scores {display: flex; flex-wrap: wrap; gap: 6px 18px; font-size: 0.8rem; color: #6E7781;
           border-top: 1px solid #EFEDE8; padding-top: 10px; margin-top: 4px;}
  .scores b {color: #1E2A36;}
  .scores .total {margin-left: auto;}
  div[class*="st-key-card"] [data-testid="stExpander"] {margin-top: 6px;}
  div[class*="st-key-card"] [data-testid="stMarkdownContainer"] {margin-bottom: 4px;}

  .plan {width: 100%; border-collapse: collapse; table-layout: fixed; background: #FFFFFF;
         border: 1px solid #E6E3DC; border-radius: 10px; overflow: hidden; font-size: 0.88rem;}
  .plan th {text-align: left; font-weight: 500; color: #8A929A; font-size: 0.74rem; letter-spacing: 0.6px;
            text-transform: uppercase; padding: 10px 14px; border-bottom: 1px solid #EFEDE8;}
  .plan td {padding: 10px 14px; vertical-align: top; color: #1E2A36; border-top: 1px solid #F3F1EC;
            overflow-wrap: anywhere;}
  .plan .c-rank {width: 64px; color: #6E7781;}
  .plan th.c-clip {font-family: inherit; font-size: 0.74rem; color: #8A929A;}
  .plan .c-clip {width: 210px; font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 0.8rem;
                 color: #4A5560;}
  .note {font-size: 0.82rem; color: #8A4F43; margin-top: 4px;}
  .section-note {color: #6E7781; font-size: 0.9rem; margin: 2px 0 14px 0;}
  .day {font-weight: 600; color: #1E2A36; margin: 22px 0 8px 0;}
  .day span {font-weight: 400; color: #6E7781;}
  .line {font-size: 0.88rem; margin: 2px 0;}
  .hidden-note {font-size: 0.86rem; color: #6E7781; background: #F7F6F3; border: 1px dashed #DDD9D0;
                border-radius: 8px; padding: 10px 14px; margin: 6px 0 10px 0;}
  .banner {font-size: 0.88rem; color: #4A5560; background: #FFFFFF; border: 1px solid #E6E3DC;
           border-radius: 10px; padding: 12px 16px; margin: -8px 0 26px 0; line-height: 1.5;}
  .line .t {font-family: ui-monospace, Menlo, Consolas, monospace; color: #8A929A; margin-right: 8px;}
  .spot-meta {margin-top: auto;}
  .spot-card {display: flex; flex-direction: column; min-width: 0;}
  .val, .spot-hook, .why {overflow-wrap: anywhere;}

  @media (max-width: 900px) {
    .stats {grid-template-columns: repeat(2, minmax(0, 1fr));}
    .spot {grid-template-columns: minmax(0, 1fr);}
    .show-title {font-size: 2rem;}
  }
  @media (max-width: 640px) {
    .scores .total {margin-left: 0;}
    .plan .c-clip {width: 120px;}
    .plan .c-rank {width: 44px;}
    .block-container {padding-left: 1rem; padding-right: 1rem;}
    .hc {grid-template-columns: minmax(0, 1fr); gap: 10px;}
    .times {margin-left: 0; white-space: normal;}
    .site-footer {flex-direction: column; gap: 6px;}
  }
</style>
""", unsafe_allow_html=True)

# Muted palette. Colours are given to angles in the order the run listed them,
# so a new angle list in config.py gets colours automatically.
PALETTE = ["#A86F6F", "#6D6A8C", "#B88A5A", "#8C6A4F", "#B07F8E", "#4F7A8C",
           "#8E5A5A", "#6B8F71", "#7A8C5A", "#5A6E8C", "#9C7FA6", "#6F8A88"]
NEUTRAL = "#8A929A"


def angle_colors(angles):
    return {a: PALETTE[i % len(PALETTE)] for i, a in enumerate(angles)}


# ---------------------------------------------------------------- loading
def find_runs(base):
    """Every folder under 'base' that holds a finished run (has candidates_all.json)."""
    base = Path(base)
    if not base.exists():
        return []
    runs = [f.parent for f in base.rglob("candidates_all.json")]
    return sorted(runs, key=lambda r: ("mock" in r.name, r.name))   # mock (fake) runs last


def session_dir():
    """A private temporary folder for this visitor only (uploads and online runs)."""
    if "tmp" not in st.session_state:
        st.session_state.tmp = Path(tempfile.mkdtemp(prefix="promo_"))
    return st.session_state.tmp


def safe_extract(uploaded, target):
    """Unzip an uploaded file, refusing paths that would escape the folder."""
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(uploaded) as z:
        for name in z.namelist():
            if name.startswith("/") or ".." in Path(name).parts:
                raise ValueError(f"Unsafe path inside zip: {name}")
        z.extractall(target)
    return target


def run_version(folder):
    """Changes whenever the run's files change, so the cache never shows old results."""
    return tuple(f.stat().st_mtime for f in sorted(Path(folder).glob("*.*")))


@st.cache_data
def load_run(folder, version):
    folder = Path(folder)
    promos = json.loads((folder / "candidates_all.json").read_text(encoding="utf-8"))
    summary = {}
    if (folder / "run_summary.json").exists():
        summary = json.loads((folder / "run_summary.json").read_text(encoding="utf-8"))
    plan = []
    if (folder / "posting_plan.csv").exists():
        with open(folder / "posting_plan.csv", encoding="utf-8") as f:
            plan = list(csv.DictReader(f))
    # Same order as the final ranking: kept promos, best score first
    kept = [p for p in promos if p["status"] == "valid" and p.get("final_score") is not None]
    kept.sort(key=lambda p: p["final_score"], reverse=True)
    for rank, p in enumerate(kept, 1):
        p["rank"] = rank
    return promos, summary, plan


@st.cache_data
def load_script(candidates):
    """Screenplay lines by episode from the first folder that exists.
    Empty if no screenplays are available (the dashboard still works)."""
    for folder in candidates:
        if folder and Path(folder).exists():
            show = load_show(folder)
            if show:
                return {ep["number"]: ep["lines"] for ep in show}
    return {}


def segments_text(p):
    return "  +  ".join(f"Ep {s['episode']}  {fmt_time(s['start'])} – {fmt_time(s['end'])}"
                        for s in p.get("segments", []))


def clean(text):
    return str(text or "").strip().strip('"')


# ---------------------------------------------------------------- display
def timeline_html(p, accent):
    """One thin bar per episode the promo uses; shaded parts = the clip."""
    rows = []
    for ep in sorted({seg["episode"] for seg in p.get("segments", [])}):
        length = EP_DURATION.get(ep)
        if not length:
            return ""
        segs = "".join(
            f'<div class="tl-seg" style="left:{100 * seg["start"] / length:.1f}%;'
            f'width:{max(1.5, 100 * (seg["end"] - seg["start"]) / length):.1f}%"></div>'
            for seg in p["segments"] if seg["episode"] == ep)
        rows.append(f'<div class="tl-row"><span class="tl-ep">Ep {ep}</span><div class="tl-bar">{segs}</div>'
                    f'<span class="tl-end">{fmt_time(length)[:5]}</span></div>')
    return f'<div class="timeline">{"".join(rows)}</div>' if rows else ""


def promo_card(p, script, key):
    accent = ANGLE_COLORS.get(p.get("angle", "other"), NEUTRAL)
    if p["status"] != "valid":
        accent = "#C9C4BA"
    tags = []
    if p.get("rank"):
        tags.append(f'<span class="tag rank">#{p["rank"]}</span>')
    elif p["status"] == "rejected":
        tags.append('<span class="tag rej">rejected</span>')
    elif p["status"] == "duplicate":
        tags.append('<span class="tag dup">duplicate</span>')
    if p.get("angle") and p["angle"] != "other":
        tags.append(f'<span class="tag">{p["angle"]}</span>')
    if p.get("duration"):
        tags.append(f'<span class="tag">{p["duration"]:.0f} s</span>')
    if len({s["episode"] for s in p.get("segments", [])}) > 1:
        tags.append('<span class="tag">two episodes</span>')
    if p.get("self_corrected"):
        tags.append('<span class="tag" title="Rejected at first, fixed by the AI after the rule checker '
                    'told it what was wrong">self-corrected</span>')
    times = f'<span class="times">{segments_text(p)}</span>' if p.get("segments") else ""

    html = f'<div class="row">{" ".join(tags)}{times}</div>'
    html += timeline_html(p, accent)
    if REDACTED:
        html += f'<div class="hidden-note">{HIDDEN}</div>'
    else:
        html += (f'<div class="hc"><div><div class="label">Hook</div><div class="val">{clean(p.get("hook")) or "—"}</div></div>'
                 f'<div><div class="label">Cliffhanger</div><div class="val">{clean(p.get("cliffhanger")) or "—"}</div></div></div>')
    if p.get("why"):
        html += f'<div class="why">{p["why"]}</div>'
    if p["status"] in ("rejected", "duplicate"):
        html += f'<div class="note">{"; ".join(p["reasons"])}</div>'
    if p.get("score_hook") is not None:
        html += (f'<div class="scores"><span>Hook <b>{p["score_hook"]:.0f}</b>/10</span>'
                 f'<span>Cliffhanger <b>{p["score_cliffhanger"]:.0f}</b>/10</span>'
                 f'<span>Makes sense alone <b>{p["score_standalone"]:.0f}</b>/10</span>'
                 + (f'<span class="total">Score <b>{p["final_score"]}</b></span>' if p.get("final_score") else "")
                 + '</div>')

    with st.container(key=f"card_{key}"):
        st.markdown(html,
                    unsafe_allow_html=True)
        if script and p.get("segments"):
            with st.expander("Read the clip"):
                out = []
                for n, seg in enumerate(p["segments"]):
                    if n:
                        out.append('<div class="line" style="color:#8A929A">— cut —</div>')
                    for line in script.get(seg["episode"], []):
                        if seg["start"] <= line["start"] < seg["end"] and line["kind"] != "camera":
                            who = line["speaker"] if line["kind"] == "dialogue" else line["kind"].lower()
                            out.append(f'<div class="line"><span class="t">{fmt_time(line["start"])}</span>'
                                       f'<b>{who}</b> {line["text"]}</div>')
                st.markdown("".join(out), unsafe_allow_html=True)


# ---------------------------------------------------------------- page
def unlock_show(locked, password):
    """Decrypt a .locked file into this visitor's private temp folder. True on success."""
    from lock_data import unlock
    try:
        data = unlock(locked.read_bytes(), password)
    except ValueError:
        return False
    target = session_dir() / "unlocked" / locked.stem
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(target)
    st.session_state[f"unlocked::{locked.name}"] = str(target)
    return True


def progress_value(lines, total):
    """How far along the run is, from the last '... episode i/N' line."""
    for line in reversed(lines):
        if line.strip().startswith("... episode") and total:
            return (int(line.split("(")[1].split("/")[0]) - 1) / total
    return 0.0


def run_online(container):
    """Upload screenplays, paste a key, run the real tool on a few episodes."""
    st.markdown('<div class="eyebrow">Promo finder</div>'
                '<div class="show-title">Run on new screenplays</div>'
                '<div class="meta">Upload a .zip of episode .txt files and use your own API key. '
                'Your key is used for this run only and never saved. Uploaded files go to a private '
                'temporary folder that other visitors cannot see.</div>',
                unsafe_allow_html=True)
    providers = [p for p in config.PROVIDERS if p != "mock"] + ["mock"]
    c1, c2 = st.columns(2)
    with c1:
        upload = st.file_uploader("Screenplays (.zip)", type="zip")
        episodes = st.text_input("Episodes", "1-3", help="Keep it small on free tiers, e.g. 1-3")
    with c2:
        default = providers.index(config.PROVIDER) if config.PROVIDER in providers else 0
        provider = st.selectbox("Provider", providers, index=default,
                                help="'custom' = any OpenAI-compatible API; 'mock' = offline fake model for testing")
        settings = config.PROVIDERS[provider]
        model = st.text_input("Model", settings.get("model", ""),
                              help="Leave as is, or type any model name your key can use")
        base_url = st.text_input("API address", settings.get("base_url", ""),
                                 disabled=provider != "custom", help="Only needed for 'custom'")
        key = st.text_input("API key", type="password", disabled=provider == "mock")
    if not st.button("Find promos", type="primary", disabled=upload is None):
        return None
    work = session_dir() / f"run_{uuid.uuid4().hex[:6]}"
    try:
        scripts = safe_extract(upload, work / "screenplays")
    except Exception as error:
        st.error(f"Could not open the zip: {error}")
        return None
    cmd = [sys.executable, "-u", "find_promos.py", "--input", str(scripts), "--episodes", episodes,
           "--no-bridges", "--provider", provider, "--run-name", "result",
           "--output-dir", str(work / "outputs")]
    if model and provider != "mock":
        cmd += ["--model", model]
    if provider == "custom" and base_url:
        cmd += ["--base-url", base_url]
    env = dict(os.environ)
    if provider != "mock":
        if not key.strip():
            st.error("Paste an API key for the chosen provider first.")
            return None
        env[settings.get("api_key_env", "LLM_API_KEY")] = key.strip()
    # Live progress: the tool prints one line per episode and one per promo found
    progress = st.progress(0.0, text="Starting...")
    found_box = st.container()
    with st.expander("Technical log", expanded=False):
        log_box = st.empty()
    lines, total, found = [], 0, 0
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            env=env, stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace")
    for line in proc.stdout:
        line = line.rstrip()
        lines.append(line)
        log_box.code("\n".join(lines[-25:]))
        if line.startswith("Show:") and "episodes" in line:
            try:
                total = int(line.split("|")[1].split()[0])
            except (IndexError, ValueError):
                total = 0
        elif line.strip().startswith("... episode") and total:
            done = int(line.split("(")[1].split("/")[0]) - 1
            progress.progress(done / total, text=line.strip().removeprefix("... ").capitalize())
        elif line.strip().startswith("Ep ") and "ideas" in line:
            found_box.markdown(f'<div class="line"><b>{line.split(":")[0].strip()}</b> · '
                               f'{line.split(":", 1)[1].strip()}</div>', unsafe_allow_html=True)
        elif line.strip().startswith("+ "):
            found += 1
            clip, _, hook = line.strip()[2:].partition(" | ")
            found_box.markdown(f'<div class="line" style="padding-left:18px">'
                               f'<span class="t">{clip}</span>{clean(hook)}</div>',
                               unsafe_allow_html=True)
        elif "Rate limit" in line or "retrying" in line or "switching" in line:
            progress.progress(progress_value(lines, total), text="Waiting for the API: " + line.strip()[:90])
    proc.wait()
    progress.progress(1.0, text=f"Finished - {found} promos found. Ranking them now...")
    result = work / "outputs" / "result"
    if proc.returncode != 0 or not (result / "candidates_all.json").exists():
        reason = next((l for l in reversed(lines) if "Error" in l or "error" in l), "")
        st.error("The run did not finish." + (f" Last error: {reason.strip()[:300]}" if reason else
                 " See the log above."))
        return None
    st.session_state.online_result = str(result)
    st.rerun()


with st.sidebar:
    st.markdown("### Results")
    sources = []
    if find_runs(PUBLIC):
        sources.append(PUBLIC_SRC)
    locked_files = sorted(PRIVATE.glob("*.locked")) if PRIVATE.exists() else []
    if locked_files:
        sources.append(LOCKED_SRC)
    if find_runs(OUTPUTS):
        sources.append("My runs")
    sources.append("Run on new screenplays")
    # One-click review link: ...streamlit.app/?key=<password> unlocks the full show
    link_key = st.query_params.get("key")
    if link_key and locked_files and "link_tried" not in st.session_state:
        st.session_state.link_tried = True
        if any(unlock_show(f, link_key) for f in locked_files):
            st.session_state.source_pick = LOCKED_SRC
    pick = st.session_state.get("source_pick")
    source = st.radio("Source", sources, label_visibility="collapsed",
                      index=sources.index(pick) if pick in sources else 0,
                      help="Kuku show: results = text-free view of the real run. "
                           "Run on new screenplays = try the tool on a new show.")

    folder, script_folders = None, []
    if source in (PUBLIC_SRC, "My runs"):
        runs = find_runs(PUBLIC if source == PUBLIC_SRC else OUTPUTS)
        folder = st.selectbox("Run", runs, format_func=lambda r: r.name)
        if "mock" in folder.name:
            st.warning("This is a mock run with fake picks, used only to test the code.")
    elif source == LOCKED_SRC:
        locked = st.selectbox("Show", locked_files, format_func=lambda f: f"Locked: {f.stem}")
        opened = st.session_state.get(f"unlocked::{locked.name}")
        if not opened:
            with st.form("unlock"):
                password = st.text_input("Password", type="password",
                                         help="The confidential show is encrypted; the password "
                                              "was shared with the reviewers.")
                if st.form_submit_button("Unlock"):
                    if unlock_show(locked, password):
                        st.rerun()
                    else:
                        st.error("Wrong password.")
        else:
            found = find_runs(Path(opened) / "results")
            if found:
                folder = st.selectbox("Run", found, format_func=lambda r: r.name)
            if (Path(opened) / "screenplays").exists():
                script_folders.append(Path(opened) / "screenplays")
    elif st.session_state.get("online_result"):
        folder = Path(st.session_state.online_result)
        if st.button("Start a new run"):
            st.session_state.pop("online_result")
            st.rerun()
    show_rejected = st.toggle("Show rejected and duplicate ideas", value=False)

if source == "Run on new screenplays" and folder is None:
    run_online(st.container())
    st.stop()
if folder is None and source == LOCKED_SRC:
    st.markdown('<div class="eyebrow">Promo finder</div><div class="show-title">Confidential show</div>'
                '<div class="meta">This show belongs to Kuku and is stored encrypted. Enter the password '
                'in the sidebar to open it.</div>', unsafe_allow_html=True)
    st.stop()
if folder is None:
    st.stop()

run = folder.name
promos, summary, plan = load_run(str(folder), run_version(folder))
REDACTED = bool(summary.get("redacted"))
input_path = summary.get("input", "")
candidates = list(script_folders)
if input_path:
    # runs made on Windows store paths with backslashes; the server is Linux
    candidates.append(Path(input_path.replace("\\", "/")))
if summary.get("show"):
    candidates.append(DATA / summary["show"])
script = load_script(tuple(str(f) for f in candidates))
settings = summary.get("settings", {})
angle_list = settings.get("angles") or sorted({p.get("angle", "other") for p in promos} - {"other"})
ANGLE_COLORS = angle_colors(angle_list)

kept = sorted([p for p in promos if p.get("rank")], key=lambda p: p["rank"])
episodes = sorted({p["episode"] for p in promos})
# Episode lengths for the clip timelines: from the screenplays if available,
# otherwise from the run summary (newer runs record them)
EP_DURATION = {int(k): v for k, v in summary.get("episode_durations", {}).items()}
EP_DURATION.update({ep: max(l["end"] for l in lines) for ep, lines in script.items() if lines})
n_rej = sum(p["status"] == "rejected" for p in promos)
st.markdown(
    f'<div class="eyebrow">Promo finder</div>'
    f'<div class="show-title">{summary.get("show", "Results")}</div>'
    f'<div class="meta">{summary.get("model", "?")} · {summary.get("run_started", "")} · run “{run}”'
    + (f' · clips {settings["min_seconds"]:g}–{settings["max_seconds"]:g} s, hook within {settings["hook_window_seconds"]:g} s'
       if settings else "") + '</div>'
    f'<div class="stats">'
    f'<div class="stat"><div class="n">{summary.get("episodes", len(episodes))}</div><div class="l">Episodes read</div></div>'
    f'<div class="stat"><div class="n">{len(promos)}</div><div class="l">Ideas from the model</div></div>'
    f'<div class="stat"><div class="n">{n_rej}</div><div class="l">Rejected by rule checks</div></div>'
    f'<div class="stat"><div class="n">{len(kept)}</div><div class="l">Final promos</div></div>'
    f'</div>', unsafe_allow_html=True)

if REDACTED:
    st.markdown('<div class="banner">These are my real results on Kuku\'s show: every promo\'s episode, '
                'timestamps, length, angle, scores and rank. The dialogue is hidden because the screenplays are '
                'confidential. You can check any timestamp against your copy, or open '
                f'<b>{LOCKED_SRC}</b> in the sidebar to read the full text.</div>', unsafe_allow_html=True)

if kept:
    cards = []
    for p in kept[:3]:
        first = " first" if p["rank"] == 1 else ""
        eps = "+".join(str(e) for e in sorted({s_["episode"] for s_ in p["segments"]}))
        cards.append(f'<div class="spot-card{first}"><div class="spot-rank">{p["rank"]}</div>'
                     f'<div class="spot-hook">{segments_text(p) if REDACTED else clean(p.get("hook"))}</div>'
                     f'<div class="spot-meta">Ep {eps} · {p["duration"]:.0f} s · {p.get("angle", "")}'
                     f' · score {p["final_score"]}</div></div>')
    st.markdown('<div class="spot-head">Post these first</div><div class="spot">' + "".join(cards) + '</div>',
                unsafe_allow_html=True)

tab_ep, tab_rank, tab_plan, tab_quality = st.tabs(
    ["By episode", "Ranking", "Posting plan", "Rejected"])

with tab_ep:
    def ep_label(n):
        k = sum(1 for p in kept if p["episode"] == n)
        return f"Episode {n}  ·  {k} promo{'s' if k != 1 else ''}"

    # Every episode the run read - including ones where the model found nothing
    all_eps = summary.get("episode_numbers") or (
        list(range(min(episodes), max(episodes) + 1)) if episodes else [])
    ep = st.selectbox("Episode", all_eps, format_func=ep_label)
    mine = [p for p in promos if p["episode"] == ep]
    mine.sort(key=lambda p: (p.get("rank") or 10_000, p["status"]))
    shown = mine if show_rejected else [p for p in mine if p.get("rank")]
    if not shown:
        st.caption("No promo from this episode made the final list."
                 + ("" if show_rejected else " Turn on “Show rejected” in the sidebar to see what the model suggested."))
    for p in shown:
        promo_card(p, script, f"ep_{p['id']}")

with tab_rank:
    note = "All final promos, best first."
    if settings.get("early_episode_bonus"):
        note += " Early, low-spoiler episodes get a small ranking bonus."
    st.markdown(f'<div class="section-note">{note}</div>', unsafe_allow_html=True)
    top_n = len(kept)
    if len(kept) > 10:
        top_n = st.slider("How many to show", 1, len(kept), min(20, len(kept)))
    for p in kept[:top_n]:
        promo_card(p, script, f"rk_{p['id']}")

with tab_plan:
    if not plan:
        st.write("No posting plan in this run.")
    else:
        per_day = settings.get("promos_per_day") or max(
            sum(1 for r in plan if r["day"] == plan[0]["day"]), 1)
        days = len({r["day"] for r in plan})
        clips = "its best clip" if per_day == 1 else f"its {per_day} best clips"
        st.markdown(f'<div class="section-note">{per_day} promo{"s" if per_day != 1 else ""} a day for {days} days. '
                    f'Each day tests one angle with {clips}, so we learn which angle pulls new viewers for this show.</div>',
                    unsafe_allow_html=True)
        by_day = {}
        for row in plan:
            by_day.setdefault(row["day"], []).append(row)
        for day, rows in by_day.items():
            color = ANGLE_COLORS.get(rows[0]["angle"], NEUTRAL)
            st.markdown(f'<div class="day">Day {day} <span>· {rows[0]["angle"]}</span></div>', unsafe_allow_html=True)
            body = "".join(
                f'<tr><td class="c-rank">#{r["overall_rank"]}</td>'
                f'<td class="c-clip">{r["segments"].replace(" + ", "<br>")}</td>'
                f'<td>{clean(r.get("hook")) or "—"}</td><td>{clean(r.get("cliffhanger")) or "—"}</td></tr>'
                for r in rows)
            if REDACTED:
                body = "".join(
                    f'<tr><td class="c-rank">#{r["overall_rank"]}</td>'
                    f'<td class="c-clip">{r["segments"].replace(" + ", "<br>")}</td>'
                    f'<td>{r.get("score", "")}</td><td>{r.get("why_this_day", "")}</td></tr>' for r in rows)
                st.markdown('<table class="plan"><colgroup><col class="c-rank"><col class="c-clip"><col><col>'
                            '</colgroup><thead><tr><th class="c-rank">Rank</th><th class="c-clip">Clip</th>'
                            f'<th>Score</th><th>Why this day</th></tr></thead><tbody>{body}</tbody></table>',
                            unsafe_allow_html=True)
                continue
            st.markdown('<table class="plan"><colgroup><col class="c-rank"><col class="c-clip"><col><col>'
                        '</colgroup><thead><tr><th class="c-rank">Rank</th><th class="c-clip">Clip</th>'
                        f'<th>Hook</th><th>Cliffhanger</th></tr></thead><tbody>{body}</tbody></table>',
                        unsafe_allow_html=True)

with tab_quality:
    rejected = [p for p in promos if p["status"] == "rejected"]
    reasons = summary.get("rejection_reasons", {})
    st.markdown(f'<div class="section-note">The model suggested {len(promos)} promos. Plain Python rules rejected {len(rejected)} of them — timing is checked by code, not trusted to the model.</div>', unsafe_allow_html=True)
    if reasons:
        st.table([{"Reason": k, "Count": v} for k, v in
                  sorted(reasons.items(), key=lambda kv: -kv[1])])
    if rejected:
        with st.expander(f"See all {len(rejected)} rejected ideas"):
            for p in rejected:
                st.markdown(f"Ep {p['episode']} · {'; '.join(p['reasons'])}"
                            + ("" if REDACTED else f" · _{clean(p.get('hook'))[:80]}_"))

st.markdown(
    f'<div class="site-footer"><span>Promo finder · built by {getattr(config, "AUTHOR", "Sapna")}</span>'
    f'<a href="{getattr(config, "PROJECT_URL", "https://github.com/sapnakinha/kuku-promo-finder")}" '
    f'target="_blank">Code and run steps on GitHub</a></div>',
    unsafe_allow_html=True)
