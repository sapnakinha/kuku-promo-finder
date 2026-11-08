"""
lock_data.py - pack confidential results + screenplays into ONE encrypted file.

    python lock_data.py --results outputs/groq_full --screenplays "data/<show folder>"

You choose a password (asked twice, typing hidden). The output goes to
private/<run folder name>.locked. Without the password the file is unreadable, so it can
sit in a public GitHub repo; the dashboard's "Kuku show" option unlocks it in
memory when a viewer types the password. Give the password ONLY to the people
allowed to see the show (e.g. in the submission form), never in the repo.

Encryption: Fernet (AES + HMAC) with a key derived from the password by
PBKDF2-SHA256 (600,000 rounds) and a random salt.
"""

import argparse
import base64
import getpass
import io
import json
import os
import re
import zipfile
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

MAGIC = b"PFLOCK1"
ROUNDS = 600_000
RESULT_FILES = ("candidates_all.json", "run_summary.json", "posting_plan.csv",
                "promos_ranked.csv", "promos_ranked.md")


def key_from_password(password, salt):
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ROUNDS)
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


def lock(zip_bytes, password):
    salt = os.urandom(16)
    return MAGIC + salt + Fernet(key_from_password(password, salt)).encrypt(zip_bytes)


def unlock(blob, password):
    """Return the zip bytes, or raise ValueError on a wrong password / damaged file."""
    if not blob.startswith(MAGIC):
        raise ValueError("Not a locked promo-finder file.")
    salt, token = blob[len(MAGIC):len(MAGIC) + 16], blob[len(MAGIC) + 16:]
    try:
        return Fernet(key_from_password(password, salt)).decrypt(token)
    except InvalidToken:
        raise ValueError("Wrong password.") from None


def main():
    ap = argparse.ArgumentParser(description="Encrypt results + screenplays for a public demo.")
    ap.add_argument("--results", required=True, help="a run folder, e.g. outputs/groq_full")
    ap.add_argument("--screenplays", help="folder of the show's episode .txt files (for 'Read the clip')")
    ap.add_argument("--out", help="output file (default: private/<run folder name>.locked)")
    args = ap.parse_args()

    results = Path(args.results)
    if not (results / "candidates_all.json").exists():
        raise SystemExit(f"{results} is not a finished run (no candidates_all.json).")
    show = json.loads((results / "run_summary.json").read_text(encoding="utf-8")).get("show", results.name)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for name in RESULT_FILES:
            if (results / name).exists():
                z.write(results / name, f"results/{results.name}/{name}")
        n_scripts = 0
        if args.screenplays:
            for f in sorted(Path(args.screenplays).rglob("*.txt")):
                z.write(f, f"screenplays/{f.relative_to(args.screenplays)}")
                n_scripts += 1

    password = getpass.getpass("Choose a password (min 12 characters, typing hidden): ")
    if len(password) < 12:
        raise SystemExit("Too short - use at least 12 characters.")
    if getpass.getpass("Type it again: ") != password:
        raise SystemExit("The two passwords did not match.")

    # Named after the run folder, so not even the show's title is visible publicly
    safe = re.sub(r"[^A-Za-z0-9]+", "_", results.name).strip("_") or "show"
    out = Path(args.out or f"private/{safe}.locked")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(lock(buffer.getvalue(), password))
    assert unlock(out.read_bytes(), password) == buffer.getvalue()    # check it opens again
    print(f"Locked {results.name} + {n_scripts} screenplay files -> {out} "
          f"({out.stat().st_size / 1024:.0f} KB). Keep the password out of GitHub.")


if __name__ == "__main__":
    main()
