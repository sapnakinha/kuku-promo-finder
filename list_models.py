"""
list_models.py - check an API before using it.

    python list_models.py groq                       # which models can my key use?
    python list_models.py groq --test                # ...and send one tiny test message
    python list_models.py custom --base-url https://their.api/v1 --test --model their-model

Uses the same settings (config.py, .env) as the main program, so if this
works, find_promos.py will work too.
"""

import argparse
import sys

import config
from llm_client import LLM

SKIP = ("whisper", "tts", "embed", "guard", "audio", "transcribe", "image", "vision-only", "lyria")

ap = argparse.ArgumentParser(description="List the models an API key can use, and test it.")
ap.add_argument("provider", nargs="?", default=config.PROVIDER, help=f"one of: {', '.join(config.PROVIDERS)}")
ap.add_argument("--base-url", help="API address (for the custom provider)")
ap.add_argument("--model", help="model to use for --test")
ap.add_argument("--test", action="store_true", help="send one tiny message to check the key works")
args = ap.parse_args()

if args.provider == "mock":
    sys.exit("The mock provider is a fake, offline model - it has no model list.")

# A model name is required to build the client; any placeholder works for listing.
llm = LLM(args.provider, args.model or config.PROVIDERS.get(args.provider, {}).get("model") or "list",
          args.base_url)
try:
    names = sorted(m.id for m in llm.client.models.list())
    chat = [n for n in names if not any(s in n.lower() for s in SKIP)]
    print(f"{len(names)} models on this key. Likely chat models ({len(chat)}):")
    for n in chat:
        print("  ", n.removeprefix("models/"))
except Exception as error:
    print(f"Could not list models ({type(error).__name__}: {str(error)[:120]}).")
    print("Some APIs do not offer a model list - use --test with --model instead.")

if args.test:
    try:
        data, _ = llm.ask_json("Answer only with JSON.", 'Reply with {"ok": true}.')
        print(f"Test message to {llm.model}: OK ->", data)
    except Exception as error:
        sys.exit(f"Test message to {llm.model} FAILED: {type(error).__name__}: {str(error)[:200]}")
