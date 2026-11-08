"""
export_public.py - make a PUBLIC copy of a run with all of the show's text removed.

    python export_public.py --results outputs/groq_full

Keeps what shows the work: episodes, timestamps, lengths, angles, scores,
ranks, rejection reasons, the posting plan (by timestamps) and run statistics.
Removes everything that quotes or describes the show: hooks, cliffhangers,
"why it works", critic comments, the model's raw answers, the show's title and
the input path. The result goes to public/<run name>/ and is safe to commit;
the dashboard shows it as "Kuku show - results".
"""

import argparse
import csv
import json
from pathlib import Path

TEXT_FIELDS = ("hook", "cliffhanger", "why", "critic_comment", "critic_confusing", "model_answer")
PLAN_TEXT_COLUMNS = ("hook", "cliffhanger")


def main():
    ap = argparse.ArgumentParser(description="Public, text-free copy of a run.")
    ap.add_argument("--results", required=True, help="a run folder, e.g. outputs/groq_full")
    ap.add_argument("--name", default="Kuku show", help="title shown instead of the show's name")
    args = ap.parse_args()

    src = Path(args.results)
    if not (src / "candidates_all.json").exists():
        raise SystemExit(f"{src} is not a finished run (no candidates_all.json).")
    out = Path("public") / src.name
    out.mkdir(parents=True, exist_ok=True)

    promos = json.loads((src / "candidates_all.json").read_text(encoding="utf-8"))
    for p in promos:
        for field in TEXT_FIELDS:
            p.pop(field, None)
    (out / "candidates_all.json").write_text(json.dumps(promos, indent=1), encoding="utf-8")

    summary = json.loads((src / "run_summary.json").read_text(encoding="utf-8"))
    summary["show"] = args.name
    summary.pop("input", None)
    summary["redacted"] = True
    (out / "run_summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")

    if (src / "posting_plan.csv").exists():
        with open(src / "posting_plan.csv", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        with open(out / "posting_plan.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=[c for c in rows[0] if c not in PLAN_TEXT_COLUMNS]
                               if rows else ["day"])
            w.writeheader()
            for r in rows:
                w.writerow({k: v for k, v in r.items() if k not in PLAN_TEXT_COLUMNS})

    print(f"Public copy written to {out}  ({len(promos)} promos, all show text removed).")


if __name__ == "__main__":
    main()
