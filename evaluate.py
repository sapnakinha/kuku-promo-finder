"""
evaluate.py - how good are the tool's picks? Two checks.

1) Against YOUR hand-picked promos (the "gold set"):

    python evaluate.py --run outputs/<run folder> --gold gold/my_picks.csv

   Fill gold/my_picks.csv yourself BEFORE looking at the tool's output:
   read a few episodes and write down the promos an editor would choose.
   A tool promo "matches" one of your picks if it is in the same episode
   and the two clips overlap by at least half of the shorter one.

2) Do two models agree? (e.g. Gemini vs Llama)

    python evaluate.py --run outputs/<run A> --compare outputs/<run B>
"""

import argparse
import csv
from pathlib import Path

MATCH_OVERLAP = 0.5
TOP_K = (10, 20)


def to_seconds(text):
    """'01:05.5' or '65.5' -> 65.5"""
    text = text.strip()
    if ":" in text:
        minutes, seconds = text.split(":")
        return int(minutes) * 60 + float(seconds)
    return float(text)


def load_ranked(run_folder):
    """Read promos_ranked.csv -> list of {rank, episode, spans, hook}"""
    promos = []
    with open(Path(run_folder) / "promos_ranked.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            spans = []
            for part in row["segments"].split(" + "):        # "Ep7 00:42.0-01:15.0"
                ep, times = part.split(" ")
                start, end = times.split("-")
                spans.append((int(ep[2:]), to_seconds(start), to_seconds(end)))
            promos.append({"rank": int(row["rank"]), "spans": spans,
                           "hook": row["hook"], "score": row["score"]})
    return promos


def load_gold(path):
    """Read your hand picks. Rows that share the same 'promo' number are ONE
    promo made of several segments (e.g. a hook in Ep2 + a cliffhanger in Ep3).
    Without a 'promo' column, every row is its own promo."""
    gold, by_number = [], {}
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f, skipinitialspace=True)
        for n, row in enumerate(reader):
            row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
            if not row.get("episode"):
                continue
            span = (int(row["episode"]), to_seconds(row["start"]), to_seconds(row["end"]))
            number = row.get("promo") or f"row{n}"
            if number in by_number:                      # another segment of the same promo
                by_number[number]["spans"].append(span)
                if row.get("note"):
                    by_number[number]["note"] += " / " + row["note"]
            else:
                by_number[number] = {"spans": [span], "note": row.get("note", "")}
                gold.append(by_number[number])
    return gold


def overlap(a, b):
    """Share of the shorter clip that the two clips have in common."""
    shared = sum(max(0.0, min(x[2], y[2]) - max(x[1], y[1]))
                 for x in a["spans"] for y in b["spans"] if x[0] == y[0])
    length_a = sum(s[2] - s[1] for s in a["spans"])
    length_b = sum(s[2] - s[1] for s in b["spans"])
    return shared / max(0.1, min(length_a, length_b))


def best_match(item, promos):
    scored = [(overlap(item, p), p) for p in promos]
    scored = [x for x in scored if x[0] >= MATCH_OVERLAP]
    if not scored:
        return None
    # Prefer the best-ranked match; gold picks have no rank, so fall back to overlap
    return min(scored, key=lambda x: (x[1].get("rank", 0), -x[0]))[1]


def describe(item):
    return " + ".join(f"Ep{e} {s:.1f}s-{t:.1f}s" for e, s, t in item["spans"])


def evaluate_gold(run, gold_path):
    promos, gold = load_ranked(run), load_gold(gold_path)
    if not gold:
        raise SystemExit(f"{gold_path} has no picks yet - fill it in first.")
    gold_episodes = {s[0] for g in gold for s in g["spans"]}
    print(f"Your picks: {len(gold)} promos across episodes {sorted(gold_episodes)}\n")

    found_ranks = []
    for g in gold:
        match = best_match(g, promos)
        found_ranks.append(match["rank"] if match else None)
        status = f"FOUND at rank {match['rank']}" if match else "MISSED"
        print(f"  {describe(g):30s} {status:20s} {g['note']}")

    print("\nRecall (how many of your picks the tool also found):")
    hits = [r for r in found_ranks if r is not None]
    print(f"  anywhere in the list: {len(hits)}/{len(gold)}")
    for k in TOP_K:
        print(f"  in the tool's top {k:<3}: {sum(r <= k for r in hits)}/{len(gold)}")

    # Precision only makes sense on episodes you actually checked
    in_scope = [p for p in promos if {s[0] for s in p["spans"]} <= gold_episodes]
    agreed = [p for p in in_scope if best_match(p, gold)]
    print(f"\nPrecision on the episodes you checked: {len(agreed)}/{len(in_scope)} of the "
          f"tool's promos there match one of your picks.")
    print("  (A non-match is not always wrong - read those clips and decide;\n"
          "   good ones you missed tell you to update your gold set.)")


def compare_runs(run_a, run_b, k=10):
    a, b = load_ranked(run_a), load_ranked(run_b)
    top_a, top_b = a[:k], b[:k]
    shared = [p for p in top_a if best_match(p, top_b)]
    print(f"Top {k} of {Path(run_a).name} vs top {k} of {Path(run_b).name}: "
          f"{len(shared)}/{k} promos in common\n")
    for p in top_a:
        twin = best_match(p, b)
        where = f"rank {twin['rank']} in B" if twin else "not in B at all"
        print(f"  A#{p['rank']:<3} {describe(p):32s} -> {where}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Evaluate a promo-finder run.")
    ap.add_argument("--run", required=True, help="output folder of a run")
    ap.add_argument("--gold", help="CSV of your hand-picked promos")
    ap.add_argument("--compare", help="output folder of a second run to compare with")
    args = ap.parse_args()
    if args.gold:
        evaluate_gold(args.run, args.gold)
    if args.compare:
        compare_runs(args.run, args.compare)
    if not args.gold and not args.compare:
        ap.error("give --gold and/or --compare")
