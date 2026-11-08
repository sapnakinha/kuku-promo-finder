"""
llm_client.py - the ONLY file that talks to an AI model.

Almost every AI company (Google Gemini, Groq, OpenRouter, OpenAI, Anthropic,
Together, a company's own server...) accepts the same "OpenAI-style" request,
so one small class works for all of them.

Ways to change the model or API, from quickest to most permanent:
  1. Command line, nothing to edit:
       --provider groq --model openai/gpt-oss-120b
       --provider custom --base-url https://their.api/v1 --model their-model
     If no key is stored, the program asks for it (typing stays hidden).
  2. .env file: LLM_BASE_URL / LLM_MODEL / LLM_API_KEY for the "custom" provider.
  3. config.py: PROVIDER and the PROVIDERS table.

Robustness built in:
  - waits between calls and retries on "too many requests" / server busy
  - if a model hits its DAILY limit, switches to the next model in that
    provider's fallback_models list and keeps going
  - if an API does not support JSON mode, retries without it
"""

import getpass
import json
import os
import re
import sys
import time

import config

try:
    from dotenv import load_dotenv
    load_dotenv()          # reads your secret keys from the .env file
except ImportError:
    pass

DAILY_LIMIT_HINTS = ("per day", "perday", "tokens per day", "tpd", "rpd", "daily")


class LLM:
    def __init__(self, provider=None, model=None, base_url=None):
        self.provider = provider or config.PROVIDER
        if self.provider not in config.PROVIDERS:
            raise SystemExit(f"Unknown provider '{self.provider}'. "
                             f"Choose one of: {', '.join(config.PROVIDERS)}")
        settings = config.PROVIDERS[self.provider]
        self.calls = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self._last_call = 0.0
        self.json_mode = True
        self.models_used = []

        if self.provider == "mock":
            self.model, self.client, self.fallbacks = "mock", None, []
            self.models_used = ["mock"]
            return

        base_url = (base_url or settings.get("base_url")
                    or os.getenv(settings.get("base_url_env", ""), ""))
        self.model = (model or settings.get("model")
                      or os.getenv(settings.get("model_env", ""), ""))
        if not base_url or not self.model:
            raise SystemExit(f"Provider '{self.provider}' needs a base URL and a model name. "
                             f"Pass --base-url and --model, or fill them in config.py / .env.")
        # A model chosen on the command line is used alone; the config's
        # fallback list only applies when you use the configured model.
        self.fallbacks = [] if model else list(settings.get("fallback_models", []))

        key_name = settings.get("api_key_env", "LLM_API_KEY")
        api_key = os.getenv(key_name, "")
        if not api_key and sys.stdin.isatty():
            api_key = getpass.getpass(f"API key for {self.provider} (input hidden, not saved): ").strip()
        if not api_key:
            raise SystemExit(f"No API key found. Add  {key_name}=your_key  to the .env file "
                             f"in the project folder, or run again and paste it when asked.")

        from openai import OpenAI
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.models_used = [self.model]

    # ------------------------------------------------------------------
    def _switch_to_fallback(self, reason):
        if not self.fallbacks:
            return False
        old, self.model = self.model, self.fallbacks.pop(0)
        self.models_used.append(self.model)
        print(f"    ! {old} hit its daily limit ({reason[:60]}...) - switching to {self.model}")
        return True

    def ask_json(self, system_prompt, user_prompt):
        """Send a prompt, return (parsed_json, raw_text)."""
        if self.provider == "mock":
            raw = _mock_answer(user_prompt)
            self.calls += 1
            return json.loads(raw), raw

        attempt = 0
        while True:
            # Stay under the free tier's requests-per-minute limit
            wait = config.SECONDS_BETWEEN_CALLS - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            attempt += 1
            try:
                self._last_call = time.time()
                extra = {"response_format": {"type": "json_object"}} if self.json_mode else {}
                response = self.client.chat.completions.create(
                    model=self.model, temperature=config.TEMPERATURE,
                    messages=[{"role": "system", "content": system_prompt},
                              {"role": "user", "content": user_prompt}], **extra)
                self.calls += 1
                if response.usage:
                    self.tokens_in += response.usage.prompt_tokens or 0
                    self.tokens_out += response.usage.completion_tokens or 0
                raw = response.choices[0].message.content or ""
                return extract_json(raw), raw
            except Exception as error:
                message = str(error)
                low = message.lower()
                # API does not know JSON mode -> ask again without it (prompts demand JSON anyway)
                if self.json_mode and "response_format" in low and ("400" in low or "support" in low):
                    self.json_mode = False
                    print("    ! this API has no JSON mode - continuing without it")
                    attempt -= 1
                    continue
                # The API's strict JSON check rejected the model's answer (Groq: "Failed to
                # validate JSON"). Our own reader is more forgiving, so turn the check off.
                if self.json_mode and ("failed to validate json" in low or "json_validate_failed" in low):
                    self.json_mode = False
                    print("    ! the API's strict JSON check rejected an answer - "
                          "switching to the tool's own JSON reader")
                    attempt -= 1
                    continue
                # Daily cap -> next model, without counting it as a failed attempt
                if ("429" in message and any(h in low for h in DAILY_LIMIT_HINTS)
                        and "minute" not in low):
                    if self._switch_to_fallback(message):
                        attempt = 0
                        continue
                if attempt >= config.MAX_RETRIES:
                    raise
                pause = 15 * attempt if "429" in message or "rate" in low else 5
                print(f"    ! {type(error).__name__}: {message[:110]} - retrying in {pause}s")
                time.sleep(pause)


def extract_json(text):
    """Models sometimes wrap JSON in ```json ... ``` or add a sentence. Pull out the {...}."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        inner = text[start:end + 1]
        try:
            return json.loads(inner)
        except json.JSONDecodeError:
            # most common small mistake: a stray comma before } or ]
            return json.loads(re.sub(r",\s*([}\]])", r"\1", inner))


# ----------------------------------------------------------------------
# Mock model: lets you test the whole pipeline offline, with no key.
# It uses dumb rules (a line with "!" = hook, a line with "?" = cliffhanger),
# so its picks are deliberately naive. Never submit mock output as results.
# ----------------------------------------------------------------------
_LINE_RE = re.compile(r"^((?:E\d+-)?L\d+) \[(\d+):([\d.]+)-(\d+):([\d.]+)\] ([A-Z][^:]*): (.*)$")


def _mock_answer(prompt):
    lines = []
    for row in prompt.splitlines():
        m = _LINE_RE.match(row.strip())
        if m:
            start = int(m.group(2)) * 60 + float(m.group(3))
            end = int(m.group(4)) * 60 + float(m.group(5))
            lines.append((m.group(1), start, end, m.group(6), m.group(7)))

    if "TASK: SCORE" in prompt:           # critic call
        ids = re.findall(r"^PROMO (\S+)", prompt, flags=re.MULTILINE)
        scores = []
        for n, pid in enumerate(ids):
            scores.append({"id": pid, "hook": 5 + n % 4, "cliffhanger": 6 - n % 3,
                           "standalone": 5 + n % 2, "confusing": "mock score",
                           "comment": "mock critic - not a real judgement"})
        return json.dumps({"scores": scores})

    if "TWO-EPISODE PROMO" in prompt:     # bridge call
        dialogue = [l for l in lines if l[3] not in ("CAMERA", "ACTION", "SOUND FX")]
        prefix = lines[0][0].split("-")[0] if lines else ""
        part_a = [l for l in dialogue if l[0].startswith(prefix + "-")]
        part_b = [l for l in dialogue if not l[0].startswith(prefix + "-")]
        candidates = []
        for hook in part_a:
            if "!" not in hook[4] or not part_b:
                continue
            d1 = part_a[-1][2] - hook[1]
            for cliff in part_b:
                if "?" not in cliff[4]:
                    continue
                total = d1 + (cliff[2] - part_b[0][1])
                if 20 <= total <= 60:
                    candidates.append({
                        "segments": [{"start_line": hook[0], "end_line": part_a[-1][0]},
                                     {"start_line": part_b[0][0], "end_line": cliff[0]}],
                        "hook_line": hook[0], "hook": hook[4][:80],
                        "cliffhanger_line": cliff[0], "cliffhanger": cliff[4][:80],
                        "why": "mock: bridge with ! hook in ep A, ? cliff in ep B",
                        "angle": config.ANGLES[len(candidates) % len(config.ANGLES)]})
                    break
            if len(candidates) == config.BRIDGE_CANDIDATES:
                break
        return json.dumps({"candidates": candidates})

    # finder call
    candidates = []
    dialogue = [l for l in lines if l[3] not in ("CAMERA", "ACTION", "SOUND FX")]
    for k, hook in enumerate(dialogue):
        if "!" not in hook[4]:
            continue
        ends = [l for l in dialogue if 25 <= l[2] - hook[1] <= 55 and "?" in l[4]]
        if not ends:
            continue
        cliff = ends[-1]
        candidates.append({"segments": [{"start_line": hook[0], "end_line": cliff[0]}],
                           "hook_line": hook[0], "hook": hook[4][:80],
                           "cliffhanger_line": cliff[0], "cliffhanger": cliff[4][:80],
                           "why": "mock: exclamation opens, question closes",
                           "angle": config.ANGLES[(k + len(lines)) % len(config.ANGLES)]})
        if len(candidates) == config.CANDIDATES_PER_EPISODE:
            break
    return json.dumps({"candidates": candidates})
