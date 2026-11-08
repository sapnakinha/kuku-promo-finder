"""
config.py - every setting you might want to change lives here.

To SWAP THE AI MODEL you only change PROVIDER (or pass --provider on the
command line). Every provider below speaks the same "OpenAI-style" API,
so the rest of the code never changes.
"""

# ---------------------------------------------------------------------------
# 1. Which AI model to use
# ---------------------------------------------------------------------------
PROVIDER = "groq"   # "gemini", "groq", "openrouter", "custom" or "mock"

PROVIDERS = {
    # Google Gemini - free key from https://aistudio.google.com/apikey
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "model": "gemini-3.8-flash",
        "api_key_env": "GEMINI_API_KEY",    # name of the variable in your .env file
    },
    # Groq - free key from https://console.groq.com/keys
    # Run  python list_models.py groq  to see the model names your key can use.
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "model": "openai/gpt-oss-120b",
        # Free tiers cap tokens PER DAY PER MODEL. When the model above hits its
        # cap, the tool switches to the next one here automatically.
        "fallback_models": ["qwen/qwen3.8-27b"],
        "api_key_env": "GROQ_API_KEY",
    },
    # OpenRouter (many models, some free) - https://openrouter.ai/keys
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "",                         # pass --model, e.g. one ending in :free
        "api_key_env": "OPENROUTER_API_KEY",
    },
    # OpenAI and Anthropic both accept OpenAI-style requests at these addresses.
    # Model names change often, so pass the current one with --model.
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "",
        "api_key_env": "OPENAI_API_KEY",
    },
    "anthropic": {
        "base_url": "https://api.anthropic.com/v1/",
        "model": "",
        "api_key_env": "ANTHROPIC_API_KEY",
    },
    # ANY other OpenAI-compatible API (e.g. one Kuku gives you in the interview):
    #   python find_promos.py --input ... --provider custom --base-url <URL> --model <NAME>
    # and paste the key when asked - or put LLM_BASE_URL / LLM_MODEL / LLM_API_KEY in .env.
    "custom": {
        "base_url_env": "LLM_BASE_URL",
        "model_env": "LLM_MODEL",
        "api_key_env": "LLM_API_KEY",
    },
    # Fake model with no internet and no key. Only for testing that the
    # code runs end to end. Its "promos" are NOT real picks.
    "mock": {},
}

# Free tiers allow only a few requests per minute, so we wait between calls.
SECONDS_BETWEEN_CALLS = 7
MAX_RETRIES = 5            # retries when the API says "too many requests"
TEMPERATURE = 0.3          # low = more consistent answers between runs

# ---------------------------------------------------------------------------
# 2. What counts as a valid promo (checked by plain Python, not by the AI)
# ---------------------------------------------------------------------------
MIN_PROMO_SECONDS = 20
MAX_PROMO_SECONDS = 60
HOOK_WINDOW_SECONDS = 3.0         # the hook must start within the first 3 seconds
CLIFFHANGER_WINDOW_SECONDS = 3.0  # the cliffhanger line must end within 3 s of the cut
MAX_SEGMENTS = 3                  # a promo can join at most 3 moments
CANDIDATES_PER_EPISODE = 4        # how many ideas the finder suggests per episode

# Self-correction: if an episode ends up with fewer good promos than this, the
# rejected ideas go back to the AI with the exact problem ("12 s, must be 20-60")
# and it is asked for corrected ones. 0 rounds = switch off.
TARGET_PROMOS_PER_EPISODE = 3
REPAIR_ROUNDS = 1

# Two-episode ("bridge") promos: the end of episode N joined to the start of N+1.
# Episodes end on a cliffhanger and the next one picks up there, so a hook from
# one episode plus the payoff/cliffhanger from the next is a natural promo.
BRIDGES = True
BRIDGE_TAIL_SECONDS = 60          # how much of the END of episode N the AI sees
BRIDGE_HEAD_SECONDS = 75          # how much of the START of episode N+1 it sees
BRIDGE_CANDIDATES = 2             # ideas per episode pair

# ---------------------------------------------------------------------------
# 3. How promos are ranked
# ---------------------------------------------------------------------------
# The critic scores each promo 1-10 on three things; final score = weighted sum.
WEIGHTS = {"hook": 0.40, "cliffhanger": 0.35, "standalone": 0.25}

# A promo from an early episode sends new viewers to content they can watch
# right away and spoils less. Up to this many bonus points for episode 1,
# shrinking to 0 for the last episode. Set to 0 to switch off.
EARLY_EPISODE_BONUS = 1.0   # my decision: big spoilers rank lower

# Two promos that overlap more than this fraction count as duplicates;
# only the higher-scoring one is kept.
DUPLICATE_OVERLAP = 0.5

TOP_N_IN_REPORT = 20

# ---------------------------------------------------------------------------
# 3b. Posting plan: test different "angles" instead of posting the same kind
# ---------------------------------------------------------------------------
# The angle is the emotional promise a promo makes to a stranger. Different
# angles pull in different audiences, so week 1 posts the BEST promo of each
# angle; Kuku then sees which angle wins and makes more promos like it.
ANGLES = ["betrayal", "mystery", "humiliation", "revenge", "romance",
          "secret or hidden identity", "danger or threat", "family conflict"]
POSTING_PLAN_DAYS = 7          # week 1
PROMOS_PER_DAY = 2             # my decision: 2 clips per angle per day = more confidence

# ---------------------------------------------------------------------------
# 4. Cost estimate (for the "how would this scale" part of the doc)
# ---------------------------------------------------------------------------
# The free tier costs nothing, but we estimate what a PAID model would cost
# per show from the tokens actually used. Example prices in US$ per 1 million
# tokens - CHECK the provider's current price page and update before quoting.
PRICE_PER_M_INPUT_TOKENS = 0.30
PRICE_PER_M_OUTPUT_TOKENS = 2.50
