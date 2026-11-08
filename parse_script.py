"""
parse_script.py - turns one screenplay .txt file into a clean Python structure.

A screenplay block looks like this:

    **MARA (Shaking, Firm):**
    Start: (00:13.50) | End: (00:14.30)
    "Search me if you want."

We turn every block into a dictionary ("line"):

    {"id": "L7", "kind": "dialogue", "speaker": "MARA",
     "emotion": "Shaking, Firm", "start": 13.5, "end": 14.3,
     "text": '"Search me if you want."', "scene": 1}

(The examples come from the original sample show in samples/, not from client data.)

No AI is used here: the format is regular, so plain Python is faster,
free and never makes mistakes about timestamps.
"""

import re
from pathlib import Path

LABEL_RE = re.compile(r"^\*{0,2}([A-Z][^:*]*?(?:\([^)]*\))*)\s*:\*{0,2}\s*$")   # **SPEAKER (Mood):** (bold optional)
# "Start: (01:05.50) | End: (01:07.20)" - brackets optional, hours optional (1:01:05.5)
_T = r"\(?\s*((?:\d+:)?\d+:\d+(?:\.\d+)?)\s*\)?"
TIME_RE = re.compile(rf"^Start:\s*{_T}\s*\|?\s*End:\s*{_T}", re.IGNORECASE)
SCENE_RE = re.compile(r"^SCENE\s+(\d+)")
NON_DIALOGUE = {"CAMERA": "camera", "ACTION": "action", "SOUND FX": "sound"}


def to_seconds(stamp):
    """'01:05.50' -> 65.5   and   '1:01:05.5' -> 3665.5"""
    total = 0.0
    for part in stamp.split(":"):
        total = total * 60 + float(part)
    return total


def fmt_time(seconds):
    """65.5 -> '01:05.5' (the format we print in reports)"""
    minutes = int(seconds // 60)
    return f"{minutes:02d}:{seconds - minutes * 60:04.1f}"


def split_label(label):
    """'MARA (Shaking, Firm)'          -> ('MARA', 'Shaking, Firm')
    'MR. OKAFOR (O.S.) (Grave)'     -> ('MR. OKAFOR', 'O.S., Grave')"""
    notes = re.findall(r"\(([^)]*)\)", label)
    name = re.sub(r"\([^)]*\)", "", label).strip()
    return name, ", ".join(notes)


def parse_episode(path):
    """Read one episode file and return a dict with its info and all its lines."""
    text = Path(path).read_text(encoding="utf-8")
    rows = text.splitlines()

    episode = {"file": Path(path).name, "number": None, "logline": "", "lines": []}

    # Header fields
    m = re.search(r"EPISODE NUMBER:\s*Episode\s*(\d+)", text)
    if m:
        episode["number"] = int(m.group(1))
    else:  # fall back to the file name, e.g. episode-007-....txt
        # Any number in the file name: episode-007-x.txt, Ep 7.txt, 07.txt ...
        m = re.search(r"(?:ep(?:isode)?[\s_-]*)?(\d+)", Path(path).stem, re.IGNORECASE)
        episode["number"] = int(m.group(1)) if m else None
    m = re.search(r"EPISODE LOGLINE:\s*(.+)", text)
    if m:
        episode["logline"] = m.group(1).strip()

    # Walk through the file one row at a time
    scene = 0
    i = 0
    while i < len(rows):
        row = rows[i].strip()

        scene_match = SCENE_RE.match(row)
        if scene_match:
            scene = int(scene_match.group(1))

        label_match = LABEL_RE.match(row)
        if label_match and i + 1 < len(rows):
            time_match = TIME_RE.match(rows[i + 1].strip())
            if time_match:
                label = label_match.group(1).strip()
                start = to_seconds(time_match.group(1))
                end = to_seconds(time_match.group(2))

                # The text is every row after the timestamp until a blank row
                body = []
                j = i + 2
                while j < len(rows) and rows[j].strip() != "":
                    body.append(rows[j].strip())
                    j += 1

                name, emotion = split_label(label)
                kind = NON_DIALOGUE.get(name.upper(), "dialogue")
                episode["lines"].append({
                    "id": f"L{len(episode['lines']) + 1}",
                    "kind": kind,
                    "speaker": name if kind == "dialogue" else "",
                    "emotion": emotion if kind == "dialogue" else "",
                    "start": start,
                    "end": end,
                    "text": " ".join(body),
                    "scene": scene,
                    "episode": episode["number"],
                })
                i = j
                continue
        i += 1

    if episode["lines"]:
        episode["duration"] = max(line["end"] for line in episode["lines"])
    else:
        episode["duration"] = 0.0
    return episode


def load_show(folder):
    """Parse every .txt file in a folder (and its subfolders), sorted by episode
    number. Files that contain no timed lines are skipped with a warning."""
    files = sorted(Path(folder).rglob("*.txt"), key=lambda f: str(f).lower())
    episodes = []
    for f in files:
        ep = parse_episode(f)
        if ep["lines"]:
            episodes.append(ep)
        else:
            print(f"  ! skipped {f.name}: no timed lines found (different screenplay format?)")
    # Files with no episode number anywhere get numbers in file-name order
    used = {e["number"] for e in episodes if e["number"] is not None}
    next_free = 1
    for ep in episodes:
        if ep["number"] is None:
            while next_free in used:
                next_free += 1
            ep["number"] = next_free
            used.add(next_free)
            for line in ep["lines"]:
                line["episode"] = next_free
    numbers = [e["number"] for e in episodes]
    if len(numbers) != len(set(numbers)):
        print("  ! warning: two files have the same episode number - check the file names/headers")
    return sorted(episodes, key=lambda e: e["number"])


def line_to_text(line):
    """How one line is shown to the AI:  L7 [00:13.5-00:14.3] MARA (Shaking): "Search me." """
    when = f"[{fmt_time(line['start'])}-{fmt_time(line['end'])}]"
    if line["kind"] == "dialogue":
        who = line["speaker"] + (f" ({line['emotion']})" if line["emotion"] else "")
        return f"{line['id']} {when} {who}: {line['text']}"
    return f"{line['id']} {when} {line['kind'].upper()}: {line['text']}"


def episode_to_text(episode):
    """The whole episode as numbered lines, ready to paste into a prompt."""
    return "\n".join(line_to_text(line) for line in episode["lines"])


if __name__ == "__main__":
    # Quick check:  python parse_script.py data/<show folder>
    import sys
    show = load_show(sys.argv[1])
    for ep in show:
        dialogue = sum(1 for l in ep["lines"] if l["kind"] == "dialogue")
        print(f"Episode {ep['number']:>3}: {len(ep['lines']):>3} lines "
              f"({dialogue} dialogue), {fmt_time(ep['duration'])} long")
    print(f"\n{len(show)} episodes parsed.")
