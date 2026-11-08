"""
find_promos.py - the main program.

    python find_promos.py --input "data/<show folder>"

Pipeline (what each step is and who does it):
  1. PARSE     (Python) read every screenplay into timed, numbered lines
  2. FIND      (AI)     per episode, suggest promo clips using line IDs only
  3. CHECK     (Python) turn IDs into exact timestamps, enforce the rules
                        (20-60 s, hook in first 3 s, ends on the cliffhanger),
                        auto-trim small mistakes, reject the rest with a reason
  4. CRITIC    (AI)     a second, separate call scores each clip seeing ONLY the
                        clip text - like a stranger scrolling - on hook,
                        cliffhanger and "makes sense on its own"
  5. RANK      (Python) weighted score + small early-episode bonus,
                        remove near-duplicates, write the reports
"""

import argparse
import csv
import json
import time
from datetime import datetime
from pathlib import Path

import config
import prompts
from llm_client import LLM
from parse_script import load_show, episode_to_text, fmt_time, line_to_text


# ------------------------------------------------- step 3b: SELF-CORRECTION
def describe_for_repair(promo):
    """One line telling the AI what it suggested and what the code measured."""
    answer = promo.get("model_answer", {})
    segs = ", ".join(f"{s.get('start_line')}-{s.get('end_line')}" for s in answer.get("segments") or [])
    length = f"{promo['duration']:.1f} s, " if promo.get("duration") else ""
    return (f"- lines {segs or '?'}: {length}{'; '.join(promo['reasons'])}. "
            f"Hook: {promo.get('hook', '')[:70]}")


def repair_candidates(llm, episode, promos, cache_dir, round_no):
    """Send the rejected ideas back with the measured problem; get corrected ones."""
    cache = cache_dir / f"{episode_key(episode)}_repair{round_no}.txt"
    if cache.exists():
        from llm_client import extract_json
        return extract_json(cache.read_text(encoding="utf-8")).get("candidates", [])
    rejected = [describe_for_repair(p) for p in promos if p["status"] == "rejected"]
    accepted = [f"- {segments_text(p)}" for p in promos if p["status"] == "valid"]
    wanted = max(1, config.TARGET_PROMOS_PER_EPISODE - len(accepted)) + 1
    user = prompts.REPAIR_USER.format(
        number=episode["number"], logline=episode["logline"], script=episode_to_text(episode),
        rejected="\n".join(rejected) or "- (none)", accepted="\n".join(accepted) or "- (none)",
        n=wanted, angles=", ".join(config.ANGLES), playbook=prompts.PLAYBOOK, **rule_values())
    data, raw = llm.ask_json(prompts.FINDER_SYSTEM, user)
    cache.write_text(raw, encoding="utf-8")
    return data.get("candidates", [])


# ---------------------------------------------------------------- step 2: FIND
def rule_values():
    """The promo rules from config.py, so the AI is always told the same numbers
    the rule checker enforces. Change them in config.py only."""
    def num(x):
        return int(x) if float(x).is_integer() else x
    return {"min_s": num(config.MIN_PROMO_SECONDS), "max_s": num(config.MAX_PROMO_SECONDS),
            "hook_s": num(config.HOOK_WINDOW_SECONDS), "max_seg": config.MAX_SEGMENTS}


def episode_key(episode):
    """File-name label: 'ep015' for one episode, 'ep015-016_bridge' for a pair."""
    if episode.get("bridge"):
        return f"ep{episode['number']:03d}-{episode['next']:03d}_bridge"
    return f"ep{episode['number']:03d}"


def make_bridge(ep_a, ep_b):
    """Join the END of episode A with the START of episode B into one window.
    Microdrama episodes end on a cliffhanger and the next one picks up right
    there, so some of the best promos take a hook from A and the payoff or
    cliffhanger from B. Line IDs get an episode prefix (E15-L40) so the
    rule checker always knows which episode a line belongs to."""
    tail_from = ep_a["duration"] - config.BRIDGE_TAIL_SECONDS
    lines, text = [], []
    for ep, keep, title in (
            (ep_a, lambda l: l["start"] >= tail_from, f"=== END OF EPISODE {ep_a['number']} ==="),
            (ep_b, lambda l: l["start"] < config.BRIDGE_HEAD_SECONDS,
             f"=== START OF EPISODE {ep_b['number']} ===")):
        text.append(title)
        for line in ep["lines"]:
            if keep(line):
                copy = dict(line, id=f"E{ep['number']}-{line['id']}")
                lines.append(copy)
                text.append(line_to_text(copy))
    return {"number": ep_a["number"], "next": ep_b["number"], "bridge": True,
            "logline": ep_a["logline"], "logline_next": ep_b["logline"],
            "lines": lines, "script": "\n".join(text)}


def find_candidates(llm, episode, cache_dir):
    """Ask the AI for promo ideas for one episode. Saves the raw answer."""
    cache = cache_dir / f"{episode_key(episode)}_finder.txt"
    if cache.exists():                                   # resume an interrupted run
        raw = cache.read_text(encoding="utf-8")
        from llm_client import extract_json
        return extract_json(raw).get("candidates", [])

    if episode.get("bridge"):
        user = prompts.BRIDGE_USER.format(
            a=episode["number"], b=episode["next"], logline_a=episode["logline"],
            logline_b=episode["logline_next"], script=episode["script"],
            n=config.BRIDGE_CANDIDATES, angles=", ".join(config.ANGLES),
            playbook=prompts.PLAYBOOK, **rule_values())
    else:
        user = prompts.FINDER_USER.format(
            number=episode["number"], logline=episode["logline"],
            script=episode_to_text(episode), n=config.CANDIDATES_PER_EPISODE,
            angles=", ".join(config.ANGLES), playbook=prompts.PLAYBOOK, **rule_values())
    data, raw = llm.ask_json(prompts.FINDER_SYSTEM, user)
    cache.write_text(raw, encoding="utf-8")
    return data.get("candidates", [])


# --------------------------------------------------------------- step 3: CHECK
def normalise_angle(text):
    """Keep the AI's angle only if it is one from our fixed list (else 'other')."""
    text = str(text).strip().lower()
    if not text:
        return "other"
    for angle in config.ANGLES:
        if text == angle or text in angle or angle in text:
            return angle
    return "other"



def check_candidate(candidate, episode, promo_id):
    """Turn line IDs into timestamps and apply the rules.
    Returns a promo dict with status 'valid' or 'rejected' (+ reasons)."""
    lines = episode["lines"]
    index = {line["id"]: i for i, line in enumerate(lines)}
    promo = {"id": promo_id, "episode": episode["number"], "status": "valid",
             "reasons": [], "repairs": [],
             "hook": candidate.get("hook", ""), "cliffhanger": candidate.get("cliffhanger", ""),
             "why": candidate.get("why", ""), "model_answer": candidate,
             "angle": normalise_angle(candidate.get("angle", ""))}

    def reject(reason):
        promo["status"] = "rejected"
        promo["reasons"].append(reason)
        return promo

    segments = candidate.get("segments") or []
    if not segments:
        return reject("no segments given")
    if len(segments) > config.MAX_SEGMENTS:
        return reject(f"more than {config.MAX_SEGMENTS} segments")

    # IDs -> list positions
    spans = []
    for seg in segments:
        a, b = str(seg.get("start_line", "")).strip(), str(seg.get("end_line", "")).strip()
        if a not in index or b not in index:
            return reject(f"line ID that does not exist in the script ({a} / {b})")
        if index[a] > index[b]:
            return reject(f"segment starts after it ends ({a} > {b})")
        spans.append([index[a], index[b]])
    for prev, cur in zip(spans, spans[1:]):
        if cur[0] <= prev[1]:
            return reject("segments overlap or are out of order")

    hook_id = str(candidate.get("hook_line", "")).strip()
    cliff_id = str(candidate.get("cliffhanger_line", "")).strip()
    if hook_id not in index:
        return reject(f"hook line {hook_id or '(missing)'} not in script")
    if cliff_id not in index:
        return reject(f"cliffhanger line {cliff_id or '(missing)'} not in script")

    # Repair 1: if the hook is late, start the clip on the hook (trim the build-up)
    first, last = spans[0], spans[-1]
    if not (first[0] <= index[hook_id] <= first[1]):
        return reject("hook line is not inside the first segment")
    if lines[index[hook_id]]["start"] - lines[first[0]]["start"] > config.HOOK_WINDOW_SECONDS:
        first[0] = index[hook_id]
        promo["repairs"].append("trimmed start so the clip opens on the hook")

    # Repair 2: if the clip runs on past the cliffhanger, cut right after it
    if not (last[0] <= index[cliff_id] <= last[1]):
        return reject("cliffhanger line is not inside the last segment")
    if lines[last[1]]["end"] - lines[index[cliff_id]]["end"] > config.CLIFFHANGER_WINDOW_SECONDS:
        last[1] = index[cliff_id]
        promo["repairs"].append("trimmed end so the clip cuts on the cliffhanger")

    # Exact timestamps come from the script, never from the AI
    promo["segments"] = []
    total = 0.0
    for a, b in spans:
        start, end = lines[a]["start"], lines[b]["end"]
        if end <= start:
            return reject("segment has zero length")
        if lines[a]["episode"] != lines[b]["episode"]:
            return reject("a segment runs across two episodes (split it in two)")
        promo["segments"].append({"episode": lines[a]["episode"],
                                  "start_line": lines[a]["id"], "end_line": lines[b]["id"],
                                  "start": start, "end": end})
        total += end - start
    promo["episode"] = promo["segments"][0]["episode"]
    if episode.get("bridge") and len({s["episode"] for s in promo["segments"]}) < 2:
        return reject("bridge promo uses only one episode (already covered by single-episode search)")
    promo["duration"] = round(total, 1)
    promo["hook_line"], promo["cliffhanger_line"] = hook_id, cliff_id

    if total < config.MIN_PROMO_SECONDS:
        return reject(f"too short ({total:.1f}s < {config.MIN_PROMO_SECONDS}s)")
    if total > config.MAX_PROMO_SECONDS:
        return reject(f"too long ({total:.1f}s > {config.MAX_PROMO_SECONDS}s)")
    return promo


def clip_text(promo, episode):
    """The clip as a stranger would experience it: only its own lines,
    times counted from 0 at the start of the clip."""
    lines = episode["lines"]
    index = {line["id"]: i for i, line in enumerate(lines)}
    out, offset = [], 0.0
    for n, seg in enumerate(promo["segments"]):
        if n > 0:
            out.append("--- CUT TO ---")
        for line in lines[index[seg["start_line"]]: index[seg["end_line"]] + 1]:
            t = offset + max(0.0, line["start"] - seg["start"])
            if line["kind"] == "dialogue":
                out.append(f"[{t:4.1f}s] {line['speaker']} ({line['emotion']}): {line['text']}")
            else:
                out.append(f"[{t:4.1f}s] {line['kind'].upper()}: {line['text']}")
        offset += seg["end"] - seg["start"]
    return "\n".join(out)


# -------------------------------------------------------------- step 4: CRITIC
def critique(llm, promos, episode, cache_dir, tag=""):
    """One AI call scores a batch of valid promos, seeing only the clip text.
    'tag' keeps separate batches (e.g. self-corrected promos) in separate files."""
    if not promos:
        return
    cache = cache_dir / f"{episode_key(episode)}_critic{tag}.txt"
    from llm_client import extract_json
    if cache.exists():
        data = extract_json(cache.read_text(encoding="utf-8"))
    else:
        local_ids = {f"P{k + 1}": p for k, p in enumerate(promos)}
        blocks = [f"PROMO {pid} ({p['duration']}s)\n{clip_text(p, episode)}"
                  for pid, p in local_ids.items()]
        user = prompts.CRITIC_USER.format(promos="\n\n".join(blocks), **rule_values())
        data, raw = llm.ask_json(prompts.CRITIC_SYSTEM, user)
        cache.write_text(raw, encoding="utf-8")

    by_local_id = {f"P{k + 1}": p for k, p in enumerate(promos)}
    for score in data.get("scores", []):
        promo = by_local_id.get(str(score.get("id", "")).strip())
        if not promo:
            continue
        for key in ("hook", "cliffhanger", "standalone"):
            try:
                promo[f"score_{key}"] = max(1.0, min(10.0, float(score.get(key, 0))))
            except (TypeError, ValueError):
                promo[f"score_{key}"] = None
        promo["critic_confusing"] = score.get("confusing", "")
        promo["critic_comment"] = score.get("comment", "")


# ---------------------------------------------------------------- step 5: RANK
def final_score(promo, last_episode):
    parts = [promo.get(f"score_{k}") for k in config.WEIGHTS]
    if any(p is None for p in parts):
        return None
    score = sum(promo[f"score_{k}"] * w for k, w in config.WEIGHTS.items())
    if last_episode > 1:
        score += config.EARLY_EPISODE_BONUS * (1 - (promo["episode"] - 1) / (last_episode - 1))
    return round(score, 2)


def overlap(a, b):
    """Fraction of the shorter promo that overlaps the other (same episode only)."""
    shared = sum(max(0.0, min(x["end"], y["end"]) - max(x["start"], y["start"]))
                 for x in a["segments"] for y in b["segments"]
                 if x["episode"] == y["episode"])
    return shared / min(a["duration"], b["duration"])


def segments_text(promo):
    return " + ".join(f"Ep{s['episode']} {fmt_time(s['start'])}-{fmt_time(s['end'])}"
                      for s in promo["segments"])


# ------------------------------------------------------ step 6: POSTING PLAN
def posting_plan(ranked):
    """Week-1 plan: each day tests ONE angle with its TWO best promos, so a
    single weak clip doesn't decide whether an angle works. Angles go in order
    of their best promo's score. Empty slots (an angle with only one promo, or
    more days than angles) are filled with the next-best unused promos.
    Returns a list of (day, promo, reason)."""
    per_day = getattr(config, "PROMOS_PER_DAY", 2)
    days = config.POSTING_PLAN_DAYS
    by_angle = {}
    for p in ranked:                                  # ranked is best-first
        if p["angle"] != "other":
            by_angle.setdefault(p["angle"], []).append(p)
    angle_order = sorted(by_angle, key=lambda a: by_angle[a][0]["final_score"], reverse=True)

    plan, used = [], set()

    def next_unused():
        return next((p for p in ranked if p["id"] not in used), None)

    for day in range(1, days + 1):
        if day <= len(angle_order):
            angle = angle_order[day - 1]
            picks = [(p, f"tests '{angle}' (2 clips = more confidence)")
                     for p in by_angle[angle][:per_day]]
        else:
            picks = []
        for p, _ in picks:
            used.add(p["id"])
        while len(picks) < per_day:
            p = next_unused()
            if p is None:
                break
            used.add(p["id"])
            picks.append((p, "next-best promo overall (fills the slot)"))
        for p, reason in picks:
            plan.append((day, p, reason))
    return plan


def plan_description():
    per_day = getattr(config, "PROMOS_PER_DAY", 1)
    clips = "its best clip" if per_day == 1 else f"its {per_day} best clips"
    return (f"{per_day} promo{'s' if per_day != 1 else ''} per day for "
            f"{config.POSTING_PLAN_DAYS} days. Each day tests one angle (the emotional "
            f"promise to a new viewer) with {clips}, so we learn which angle pulls "
            "viewers for this show before making more promos like it.")


# ---------------------------------------------------------------- reports
def write_reports(out_dir, ranked, all_promos, summary):
    plan = posting_plan(ranked)
    with open(out_dir / "posting_plan.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["day", "angle", "overall_rank", "score", "segments", "hook",
                    "cliffhanger", "why_this_day"])
        for day, p, reason in plan:
            w.writerow([day, p["angle"], ranked.index(p) + 1, p["final_score"],
                        segments_text(p), p["hook"], p["cliffhanger"], reason])

    with open(out_dir / "candidates_all.json", "w", encoding="utf-8") as f:
        json.dump(all_promos, f, indent=2, ensure_ascii=False)

    with open(out_dir / "promos_ranked.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rank", "score", "angle", "episode", "segments", "duration_s", "hook",
                    "cliffhanger", "why_it_works", "hook_score", "cliffhanger_score",
                    "standalone_score", "critic_comment", "confusing_for_new_viewers", "auto_repairs"])
        for r, p in enumerate(ranked, 1):
            w.writerow([r, p["final_score"], p["angle"], p["episode"], segments_text(p),
                        p["duration"], p["hook"], p["cliffhanger"], p["why"], p.get("score_hook"),
                        p.get("score_cliffhanger"), p.get("score_standalone"),
                        p.get("critic_comment", ""), p.get("critic_confusing", ""),
                        "; ".join(p["repairs"])])

    md = [f"# Ranked promos - {summary['show']}", "",
          f"Model: `{summary['provider']} / {summary['model']}` - run {summary['run_started']}", "",
          f"{summary['candidates_proposed']} candidates proposed, "
          f"{summary['rejected']} rejected by rule checks, "
          f"{summary['duplicates_removed']} duplicates removed, "
          f"**{summary['final_promos']} final promos**.", "",
          "## Week-1 posting plan", "",
          plan_description(), "",
          "| Day | Angle | Rank | Clip | Hook |", "|---|---|---|---|---|"]
    for day, p, reason in plan:
        md.append(f"| {day} | {p['angle']} | #{ranked.index(p) + 1} | "
                  f"{segments_text(p)} | {p['hook']} |")
    md += ["", "## Top promos by score", ""]
    for r, p in enumerate(ranked[:config.TOP_N_IN_REPORT], 1):
        md += [f"### {r}. Score {p['final_score']} - {segments_text(p)} ({p['duration']}s)",
               f"- **Angle:** {p['angle']}",
               f"- **Hook:** {p['hook']}",
               f"- **Cliffhanger:** {p['cliffhanger']}",
               f"- **Why it works:** {p['why']}",
               f"- **Critic** (hook {p.get('score_hook')}, cliffhanger {p.get('score_cliffhanger')}, "
               f"standalone {p.get('score_standalone')}): {p.get('critic_comment', '')}",
               f"- **Might confuse new viewers:** {p.get('critic_confusing', '')}"]
        if p["repairs"]:
            md.append(f"- *Auto-fixed:* {'; '.join(p['repairs'])}")
        md.append("")
    (out_dir / "promos_ranked.md").write_text("\n".join(md), encoding="utf-8")

    with open(out_dir / "run_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)


# ---------------------------------------------------------------- main
def prepare_input(path_text):
    """Accept a folder or a .zip. A zip is extracted once into data/<zip name>/
    (data/ is git-ignored, so screenplays never go to GitHub)."""
    path = Path(path_text)
    if not path.exists():
        raise SystemExit(f"Input not found: {path}")
    if path.suffix.lower() != ".zip":
        return path
    target = Path("data") / path.stem
    if not target.exists():
        import zipfile
        with zipfile.ZipFile(path) as z:
            for member in z.namelist():                  # refuse paths that escape the folder
                if member.startswith("/") or ".." in Path(member).parts:
                    raise SystemExit(f"Unsafe path inside zip: {member}")
            z.extractall(target)
        print(f"Extracted {path.name} -> {target}")
    return target


def show_title(folder):
    """Show name = the folder name, or the single subfolder a zip usually contains."""
    folder = Path(folder)
    subdirs = [d for d in folder.iterdir() if d.is_dir() and not d.name.startswith(("_", "."))]
    if not any(folder.glob("*.txt")) and len(subdirs) == 1:
        return subdirs[0].name
    return folder.name


def parse_episode_range(text, available):
    if not text:
        return available
    wanted = set()
    for part in text.split(","):
        if "-" in part:
            a, b = part.split("-")
            wanted.update(range(int(a), int(b) + 1))
        else:
            wanted.add(int(part))
    return [n for n in available if n in wanted]


def main():
    ap = argparse.ArgumentParser(description="Find and rank promo clips in microdrama screenplays.")
    ap.add_argument("--input", required=True,
                    help="folder with the episode .txt files, or a .zip of them")
    ap.add_argument("--provider", help=f"AI provider (default: {config.PROVIDER})")
    ap.add_argument("--model", help="override the model name")
    ap.add_argument("--base-url", help="API address, for --provider custom (any OpenAI-style API)")
    ap.add_argument("--episodes", help="e.g. 1-5 or 1,3,10 (default: all)")
    ap.add_argument("--run-name", help="output folder name; reuse it to resume a stopped run")
    ap.add_argument("--output-dir", default="outputs",
                    help="where run folders are written (default: outputs)")
    ap.add_argument("--no-bridges", action="store_true",
                    help="skip two-episode promos (halves the number of AI calls)")
    args = ap.parse_args()

    started = datetime.now()
    input_path = prepare_input(args.input)
    show = load_show(input_path)
    if not show:
        raise SystemExit(f"No screenplay .txt files with timed lines found in {input_path}")
    show_name = show_title(input_path)
    numbers = parse_episode_range(args.episodes, [e["number"] for e in show])
    episodes = [e for e in show if e["number"] in numbers]
    last_episode = max(e["number"] for e in show)

    llm = LLM(args.provider, args.model, args.base_url)
    run_name = args.run_name or f"{started:%Y%m%d_%H%M}_{llm.provider}"
    out_dir = Path(args.output_dir) / run_name
    raw_dir = out_dir / "raw_model_responses"
    raw_dir.mkdir(parents=True, exist_ok=True)
    print(f"Show: {show_name} | {len(episodes)} episodes | "
          f"model: {llm.provider}/{llm.model} | output: {out_dir}")

    all_promos = []
    for i, ep in enumerate(episodes, 1):
        t0 = time.time()
        print(f"  ... episode {ep['number']} ({i}/{len(episodes)}): asking the model", flush=True)
        candidates = find_candidates(llm, ep, raw_dir)
        promos = [check_candidate(c, ep, f"E{ep['number']:03d}-C{k + 1}")
                  for k, c in enumerate(candidates)]
        valid = [p for p in promos if p["status"] == "valid"]
        critique(llm, valid, ep, raw_dir)
        fixed = 0
        for round_no in range(1, config.REPAIR_ROUNDS + 1):
            if sum(p["status"] == "valid" for p in promos) >= config.TARGET_PROMOS_PER_EPISODE:
                break
            extra = [check_candidate(c, ep, f"E{ep['number']:03d}-R{round_no}-{k + 1}")
                     for k, c in enumerate(repair_candidates(llm, ep, promos, raw_dir, round_no))]
            for p in extra:
                p["self_corrected"] = True
            new_valid = [p for p in extra if p["status"] == "valid"]
            critique(llm, new_valid, ep, raw_dir, tag=f"_repair{round_no}")
            fixed += len(new_valid)
            promos += extra
        all_promos.extend(promos)
        note = f", +{fixed} after self-correction" if fixed else ""
        print(f"  Ep {ep['number']:>3}: {len(candidates)} ideas, {len(valid)} passed checks{note} "
              f"({time.time() - t0:.0f}s)")
        shown = set()
        for p in promos:                      # one line per promo found, for live progress
            if p["status"] == "valid" and segments_text(p) not in shown:
                shown.add(segments_text(p))
                scores = (f" hook {p['score_hook']:.0f}/10, cliffhanger {p['score_cliffhanger']:.0f}/10"
                          if p.get("score_hook") is not None else "")
                print(f"      + {segments_text(p)} ({p['duration']:.0f}s) [{p['angle']}]{scores}"
                      f" | {str(p.get('hook', ''))[:90]}", flush=True)

    # Two-episode "bridge" promos: end of episode N + start of episode N+1
    if config.BRIDGES and not args.no_bridges:
        by_number = {e["number"]: e for e in episodes}
        for ep in episodes:
            nxt = by_number.get(ep["number"] + 1)
            if not nxt:
                continue
            t0 = time.time()
            bridge = make_bridge(ep, nxt)
            candidates = find_candidates(llm, bridge, raw_dir)
            promos = [check_candidate(c, bridge, f"B{ep['number']:03d}-C{k + 1}")
                      for k, c in enumerate(candidates)]
            valid = [p for p in promos if p["status"] == "valid"]
            critique(llm, valid, bridge, raw_dir)
            all_promos.extend(promos)
            print(f"  Ep {ep['number']:>3}+{nxt['number']:<3} (bridge): {len(candidates)} ideas, "
                  f"{len(valid)} passed checks ({time.time() - t0:.0f}s)")

    # Rank and remove near-duplicates (keep the better one)
    valid = [p for p in all_promos if p["status"] == "valid"]
    for p in valid:
        p["final_score"] = final_score(p, last_episode)
    scored = sorted([p for p in valid if p["final_score"] is not None],
                    key=lambda p: p["final_score"], reverse=True)
    ranked = []
    for p in scored:
        twin = next((q for q in ranked if overlap(p, q) > config.DUPLICATE_OVERLAP), None)
        if twin:
            p["status"] = "duplicate"
            p["reasons"].append(f"overlaps higher-ranked {twin['id']}")
        else:
            ranked.append(p)
    for p in valid:
        if p["final_score"] is None:
            p["status"] = "unscored"
            p["reasons"].append("critic gave no usable score")

    reasons = {}
    for p in all_promos:
        if p["status"] == "rejected":
            key = p["reasons"][0].split("(")[0].strip()
            reasons[key] = reasons.get(key, 0) + 1

    summary = {
        "show": show_name, "input": Path(input_path).as_posix(),
        "provider": llm.provider, "model": " -> ".join(llm.models_used) or llm.model,
        "run_started": f"{started:%Y-%m-%d %H:%M}", "episodes": len(episodes),
        "episode_numbers": [e["number"] for e in episodes],
        "episode_durations": {e["number"]: round(e["duration"], 1) for e in episodes},
        "candidates_proposed": len(all_promos),
        "rejected": sum(p["status"] == "rejected" for p in all_promos),
        "rejection_reasons": reasons,
        "auto_repaired": sum(bool(p["repairs"]) for p in all_promos if p["status"] != "rejected"),
        "duplicates_removed": sum(p["status"] == "duplicate" for p in all_promos),
        "final_promos": len(ranked),
        "self_corrected_promos": sum(bool(p.get("self_corrected")) for p in ranked),
        "final_two_episode_promos": sum(len({s["episode"] for s in p["segments"]}) > 1
                                        for p in ranked),
        "api_calls": llm.calls, "tokens_in": llm.tokens_in, "tokens_out": llm.tokens_out,
        "minutes": round((datetime.now() - started).total_seconds() / 60, 1),
        "settings": {"min_seconds": config.MIN_PROMO_SECONDS,
                     "max_seconds": config.MAX_PROMO_SECONDS,
                     "hook_window_seconds": config.HOOK_WINDOW_SECONDS,
                     "early_episode_bonus": config.EARLY_EPISODE_BONUS,
                     "angles": config.ANGLES,
                     "posting_days": config.POSTING_PLAN_DAYS,
                     "promos_per_day": getattr(config, "PROMOS_PER_DAY", 1),
                     "bridges": bool(config.BRIDGES and not args.no_bridges)},
        "estimated_cost_usd_if_paid": round(
            llm.tokens_in / 1e6 * config.PRICE_PER_M_INPUT_TOKENS
            + llm.tokens_out / 1e6 * config.PRICE_PER_M_OUTPUT_TOKENS, 4),
    }
    write_reports(out_dir, ranked, all_promos, summary)
    print(f"\nDone: {len(ranked)} promos ranked. Open {out_dir / 'promos_ranked.md'}")
    print(json.dumps({k: summary[k] for k in ("candidates_proposed", "rejected",
                                                "rejection_reasons", "auto_repaired",
                                                "duplicates_removed", "final_promos",
                                                "self_corrected_promos",
                                                "final_two_episode_promos", "api_calls",
                                                "estimated_cost_usd_if_paid")}, indent=2))


if __name__ == "__main__":
    main()
