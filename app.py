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
SAMPLES = Path("samples")          # an original sample show, safe to publish
PRIVATE = Path("private")          # encrypted (.locked) shows - need a password to open
DATA = Path("data")

st.set_page_config(page_title="Promo Finder", layout="wide")

st.markdown("""
<style>
  #MainMenu, footer, header [data-testid="stToolbar"], .stDeployButton {visibility: hidden;}
  .block-container {padding-top: 2.2rem; max-width: 1080px;}
  [data-testid="stSidebar"] {background: #EFEDE8; border-right: 1px solid #E2DFD8;}

  .eyebrow {font-size: 0.72rem; letter-spacing: 2px; text-transform: uppercase;
            color: #3E6A7A; font-weight: 600; margin-bottom: 4px;}
  .show-title {font-size: 2.05rem; font-weight: 650; color: #1E2A36; line-height: 1.15;
               letter-spacing: -0.4px; margin: 0;}
  .meta {color: #6E7781; font-size: 0.88rem; margin: 6px 0 22px 0;
         padding-bottom: 18px; border-bottom: 1px solid #E2DFD8;}

  .stats {display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-bottom: 26px;}
  .stat {background: #FFFFFF; border: 1px solid #E6E3DC; border-radius: 10px; padding: 14px 16px;
         border-top: 3px solid #3E6A7A;}
  .stat.warn {border-top-color: #B07A6E;}
  .stat.ok {border-top-color: #6B8F71;}
  .stat .n {font-size: 1.7rem; font-weight: 650; color: #1E2A36; line-height: 1.1;}
  .stat .l {font-size: 0.78rem; color: #6E7781; margin-top: 2px;}

  .stTabs [data-baseweb="tab-list"] {gap: 22px; border-bottom: 1px solid #E2DFD8;}
  .stTabs [data-baseweb="tab"] {padding: 8px 2px; color: #6E7781;}
  .stTabs [aria-selected="true"] {color: #1E2A36 !important; font-weight: 600;}
  .stTabs [data-baseweb="tab-highlight"] {background-color: #3E6A7A;}

  div[class*="st-key-card"] {background: #FFFFFF; border: 1px solid #E6E3DC;
        border-left: 4px solid var(--accent, #3E6A7A); border-radius: 10px;
        padding: 16px 20px 10px 20px; margin-bottom: 14px;
        transition: box-shadow .18s ease, transform .18s ease;}
  div[class*="st-key-card"]:hover {box-shadow: 0 6px 18px rgba(30,42,54,0.08); transform: translateY(-1px);}
  div[class*="st-key-card"].rejected {opacity: .75;}

  .row {display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 8px;}
  .tag {display: inline-block; padding: 2px 9px; border-radius: 20px; font-size: 0.74rem;
        background: #F1EFEA; color: #4A5560; border: 1px solid #E6E3DC;}
  .tag.rank {background: #1E2A36; color: #fff; border-color: #1E2A36; font-weight: 600;}
  .tag.rej {background: #F6ECE9; color: #8A4F43; border-color: #EBD7D1;}
  .tag.dup {background: #F3F3F3; color: #888;}
  .times {font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 0.86rem; color: #3E6A7A;
          margin-left: auto;}
  .hc {display: grid; grid-template-columns: 1fr 1fr; gap: 18px; margin: 6px 0 8px 0;}
  .label {color: #8A929A; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 1.2px;
          margin-bottom: 2px;}
  .val {color: #1E2A36; font-size: 0.97rem; line-height: 1.4;}
  .why {color: #4A5560; font-size: 0.88rem; margin: 4px 0 8px 0;}
  .scores {display: flex; gap: 16px; font-size: 0.8rem; color: #6E7781;
           border-top: 1px dashed #E6E3DC; padding-top: 8px;}
  .scores b {color: #1E2A36;}
  .note {font-size: 0.82rem; color: #8A4F43; margin-top: 4px;}
  .section-note {color: #6E7781; font-size: 0.9rem; margin: 2px 0 14px 0;}
  .day {font-weight: 600; color: #1E2A36; margin: 18px 0 6px 0; padding-left: 10px;
        border-left: 3px solid var(--accent, #3E6A7A);}
  .line {font-size: 0.88rem; margin: 2px 0;}
  .line .t {font-family: ui-monospace, Menlo, Consolas, monospace; color: #8A929A; margin-right: 8px;}
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
        tags.append(f'<span class="tag" style="color:{accent};border-color:{accent}55">{p["angle"]}</span>')
    if p.get("duration"):
        tags.append(f'<span class="tag">{p["duration"]:.0f} s</span>')
    if len({s["episode"] for s in p.get("segments", [])}) > 1:
        tags.append('<span class="tag">two episodes</span>')
    if p.get("self_corrected"):
        tags.append('<span class="tag" title="Rejected at first, fixed by the AI after the rule checker '
                    'told it what was wrong">self-corrected</span>')
    times = f'<span class="times">{segments_text(p)}</span>' if p.get("segments") else ""

    html = f'<div class="row">{" ".join(tags)}{times}</div>'
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
                 + (f'<span style="margin-left:auto">Score <b>{p["final_score"]}</b></span>' if p.get("final_score") else "")
                 + '</div>')

    with st.container(key=f"card_{key}"):
        st.markdown(f'<style>.st-key-card_{key} {{--accent: {accent};}}</style>' + html,
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
    if find_runs(SAMPLES):
        sources.append("Sample show")
    locked_files = sorted(PRIVATE.glob("*.locked")) if PRIVATE.exists() else []
    if locked_files:
        sources.append("Kuku show (password)")
    if find_runs(OUTPUTS):
        sources.append("My runs")
    sources += ["Upload results", "Run on new screenplays"]
    source = st.radio("Source", sources, label_visibility="collapsed",
                      help="Sample show: an original demo script. Upload results: a zipped run folder.")

    folder, script_folders = None, []
    if source in ("Sample show", "My runs"):
        runs = find_runs(SAMPLES if source == "Sample show" else OUTPUTS)
        folder = st.selectbox("Run", runs, format_func=lambda r: r.name)
        if "mock" in folder.name:
            st.warning("This is a mock run with fake picks, used only to test the code.")
    elif source == "Kuku show (password)":
        locked = st.selectbox("Show", locked_files, format_func=lambda f: f"Locked: {f.stem}")
        opened = st.session_state.get(f"unlocked::{locked.name}")
        if not opened:
            with st.form("unlock"):
                password = st.text_input("Password", type="password",
                                         help="The confidential show is encrypted; the password "
                                              "was shared with the reviewers.")
                if st.form_submit_button("Unlock"):
                    from lock_data import unlock
                    try:
                        data = unlock(locked.read_bytes(), password)
                        target = session_dir() / "unlocked" / locked.stem
                        with zipfile.ZipFile(io.BytesIO(data)) as z:
                            z.extractall(target)
                        st.session_state[f"unlocked::{locked.name}"] = str(target)
                        st.rerun()
                    except ValueError as error:
                        st.error(str(error))
        else:
            found = find_runs(Path(opened) / "results")
            if found:
                folder = st.selectbox("Run", found, format_func=lambda r: r.name)
            if (Path(opened) / "screenplays").exists():
                script_folders.append(Path(opened) / "screenplays")
    elif source == "Upload results":
        up = st.file_uploader("Zipped run folder", type="zip",
                              help="Zip an outputs/<run> folder and upload it here.")
        scripts_up = st.file_uploader("Screenplays (.zip, optional)", type="zip",
                                      help="Only needed for the 'Read the clip' text.")
        if up is not None:
            found = find_runs(safe_extract(up, session_dir() / "upload" / up.name))
            if found:
                folder = st.selectbox("Run", found, format_func=lambda r: r.name)
            else:
                st.error("No run found in that zip (it needs candidates_all.json).")
        if scripts_up is not None:
            script_folders.append(safe_extract(scripts_up, session_dir() / "scripts" / scripts_up.name))
    elif st.session_state.get("online_result"):
        folder = Path(st.session_state.online_result)
        if st.button("Start a new run"):
            st.session_state.pop("online_result")
            st.rerun()
    show_rejected = st.toggle("Show rejected and duplicate ideas", value=False)

if source == "Run on new screenplays" and folder is None:
    run_online(st.container())
    st.stop()
if folder is None and source == "Kuku show (password)":
    st.markdown('<div class="eyebrow">Promo finder</div><div class="show-title">Confidential show</div>'
                '<div class="meta">This show belongs to Kuku and is stored encrypted. Enter the password '
                'in the sidebar to open it.</div>', unsafe_allow_html=True)
    st.stop()
if folder is None:
    st.markdown('<div class="eyebrow">Promo finder</div><div class="show-title">Upload a run</div>'
                '<div class="meta">Zip an <code>outputs/&lt;run&gt;</code> folder and upload it in the sidebar. '
                'It is opened in a private temporary folder that other visitors cannot see.</div>', unsafe_allow_html=True)
    st.stop()

run = folder.name
promos, summary, plan = load_run(str(folder), run_version(folder))
input_path = summary.get("input", "")
candidates = list(script_folders)
if input_path:
    candidates.append(Path(input_path))
if summary.get("show"):
    candidates.append(DATA / summary["show"])
script = load_script(tuple(str(f) for f in candidates))
settings = summary.get("settings", {})
angle_list = settings.get("angles") or sorted({p.get("angle", "other") for p in promos} - {"other"})
ANGLE_COLORS = angle_colors(angle_list)

kept = sorted([p for p in promos if p.get("rank")], key=lambda p: p["rank"])
episodes = sorted({p["episode"] for p in promos})
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
    f'<div class="stat warn"><div class="n">{n_rej}</div><div class="l">Rejected by rule checks</div></div>'
    f'<div class="stat ok"><div class="n">{len(kept)}</div><div class="l">Final promos</div></div>'
    f'</div>', unsafe_allow_html=True)

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
            st.markdown(f'<div class="day" style="--accent:{color}">Day {day} · {rows[0]["angle"]}</div>', unsafe_allow_html=True)
            st.table([{"Rank": f"#{r['overall_rank']}", "Clip": r["segments"],
                       "Hook": clean(r["hook"]), "Cliffhanger": clean(r["cliffhanger"])}
                      for r in rows])

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
                st.markdown(f"Ep {p['episode']} · {'; '.join(p['reasons'])} · "
                            f"_{clean(p.get('hook'))[:80]}_")
