# Promo Finder for Microdramas

Reads the screenplays of a vertical microdrama and suggests ranked 20–60 second promo clips: each comes with exact timestamps, the hook, the cliffhanger and one line on why it works.

> The show's screenplays are confidential and are **not** in this repository. Put your own copy in `data/` (that folder is ignored by git).

## How it works

| Step | Who does it | What happens |
|---|---|---|
| 1. Parse | Python | Each screenplay becomes numbered lines (`L1`, `L2`…) with exact start and end times |
| 2. Find | AI | For each episode, suggests up to 4 promos, **using line IDs only and never timestamps** |
| 3. Check | Python | Converts IDs to real timestamps and enforces the rules: 20–60 s, hook in the first 3 s, cut on the cliffhanger. Fixes small mistakes and rejects the rest, with a reason |
| 4. Stranger test | AI | A separate call scores each clip **seeing only the clip itself**, the way a new viewer scrolling past would: hook, cliffhanger, makes sense alone (1–10 each) |
| 5. Bridges | AI + Python | The end of episode N joined to the start of episode N+1 is searched too, because episodes cut on cliffhangers and some of the best promos span the cut (steps 2-4 run again on each pair) |
| 6. Rank | Python | Weighted score plus a small bonus for early episodes; near-duplicates are removed |
| 6. Posting plan | Python | Week 1 posts **two promos per day, one angle per day** (betrayal, mystery, revenge, romance…) so Kuku learns which angle pulls viewers for this show |

Why it's split this way: the AI judges the drama, and plain code handles timestamps and rules. Because the AI only answers with line IDs that are checked against the script, **it cannot invent a timestamp**.

## Run it

```bash
git clone https://github.com/<your-username>/kuku-promo-finder.git
cd kuku-promo-finder
pip install -r requirements.txt
cp .env.example .env          # then paste your free Gemini key into .env
```

Put the episode `.txt` files in a folder inside `data/`, then:

```bash
# quick test on 3 episodes
python find_promos.py --input "data/<show folder>" --episodes 1-3

# full show
python find_promos.py --input "data/<show folder>" --run-name gemini_full

# faster run without two-episode promos (about half the AI calls)
python find_promos.py --input "data/<show folder>" --no-bridges

# same show with a different model: a single option
python find_promos.py --input "data/<show folder>" --provider groq --run-name groq_full

# offline test run with no key and no internet (fake picks, only checks the code runs)
python find_promos.py --input "data/<show folder>" --provider mock
```

If a run stops (for example because a free-tier limit was hit), run the same command with the same `--run-name` and it continues where it left off.

## Running on a new show (e.g. in the interview)

Nothing in the code is tied to one show. Point it at the new screenplays:

```bash
# a folder of episode .txt files, or the .zip exactly as you received it
python find_promos.py --input "path/to/new_show.zip" --run-name new_show

# then look at the results
python -m streamlit run app.py
```

- **Show name** comes from the folder name; **episode numbers** from each file's `EPISODE NUMBER:` header, or from the file name (`episode-007.txt`, `Ep 7.txt`, `07.txt`), or file order.
- A `.zip` is extracted into `data/` (git-ignored, so screenplays never reach GitHub).
- Timestamps may be `MM:SS.s` or `HH:MM:SS.s`; speaker labels may be bold or plain. Files with no timed lines are skipped with a warning.
- **All rules live in `config.py`**: clip length, hook window, angles, ranking weights, spoiler bonus, posting plan size. The AI's instructions, the rule checker and the dashboard all read the same values, so one edit changes everything.
- Free tiers cap tokens per day per model. For a quick demo use `--episodes 1-5 --no-bridges`; if a model hits its cap, switch the model in `config.py` (or `--model`) and rerun the same command - finished episodes are reused.

## Changing the AI model or API (no file editing needed)

```bash
# another model on the same provider
python find_promos.py --input "data/<show>" --provider groq --model qwen/qwen3.8-27b

# ANY OpenAI-compatible API you are given - paste the key when asked (it stays hidden)
python find_promos.py --input "data/<show>" --provider custom --base-url https://their.api/v1 --model their-model

# check a key and see its model names first
python list_models.py custom --base-url https://their.api/v1 --test --model their-model
```

- Ready-made providers in `config.py`: `gemini`, `groq`, `openrouter`, `openai`, `anthropic`, `custom`, `mock`.
- A model that hits its **daily** free limit is replaced automatically by the next one in its `fallback_models` list.
- If an API has no JSON mode, the tool notices and continues without it.
- Keys live only in `.env` (git-ignored) or are typed when asked; they are never printed.

## Self-correction (3-4 promos per episode)

The AI often suggests clips that break the rules (in the real run, half were under 20 s). When an episode has fewer than `TARGET_PROMOS_PER_EPISODE` good promos, the rejected ideas go back to the AI with the exact measured problem (*"lines L22-L26: 7.8 s, too short"*), and it returns corrected ones, which pass through the same rule checker. Set `REPAIR_ROUNDS = 0` in `config.py` to switch it off.

## The dashboard (local and online)

```bash
python -m streamlit run app.py
```

Sources in the sidebar:
- **Sample show**: an original 3-episode script written for this demo (`samples/`). It is not Kuku content, so it is safe to publish.
- **Kuku show (password)**: the real show, stored encrypted (see below).
- **My runs**: everything in your local `outputs/` folder (only on your computer).
- **Upload results**: upload a zipped `outputs/<run>` folder, plus optionally the screenplays zip to read each clip.
- **Run on new screenplays**: upload a screenplays zip, choose a provider and model, paste your own key, and run the real tool on a few episodes.

**Confidential data never goes online.** `data/` (screenplays) and `outputs/` (results that quote the dialogue) are git-ignored, so the hosted app only contains code and the original sample. Reviewers who have the show load it themselves through the upload options.

## Showing results on the confidential show, safely

**Public, text-free results** (no password):
```bash
python export_public.py --results outputs/groq_full    # -> public/groq_full
```
Keeps episodes, timestamps, lengths, angles, scores, ranks, rejections and the posting plan;
removes every hook, cliffhanger, explanation, the model's raw answers and the show's title.
The dashboard shows it first, as **Kuku show: results**.

**One-click full view for reviewers:** after locking (below), share
`https://<your-app>.streamlit.app/?key=<password>` privately. It unlocks the full text automatically.

## Showing the confidential show online, safely (password-locked)

The screenplays and the results that quote them must not be published. To show them on the
public website anyway, lock them into one encrypted file:

```bash
python lock_data.py --results outputs/groq_full --screenplays "data/<show folder>"
# choose a password (12+ characters) -> creates private/groq_full.locked
```

The `.locked` file is unreadable without the password (Fernet/AES encryption, key derived
from the password with PBKDF2-SHA256, 600,000 rounds), so it can be committed. On the website,
**Kuku show (password)** asks for the password and unlocks the data in memory for that visitor
only. Share the password only with the reviewers - never in this repo.

## Output (`outputs/<run name>/`)

- `promos_ranked.md`: the week-1 posting plan and the top 20, easy to read
- `posting_plan.csv`: which two promos to post on each day of week 1, and why
- `promos_ranked.csv`: every final promo with all scores
- `candidates_all.json`: every idea, including rejected ones and the reason for each
- `raw_model_responses/`: the unedited AI answers
- `run_summary.json`: counts, API calls, tokens and the estimated cost with a paid model

## Checking quality

```bash
# compare against promos I picked by hand (gold/my_picks.csv - kept local and
# git-ignored, because the notes describe the confidential show's plot)
python evaluate.py --run outputs/gemini_full --gold gold/my_picks.csv

# do two models agree on the top 10?
python evaluate.py --run outputs/gemini_full --compare outputs/groq_full
```

## Swapping the model

All model settings are in `config.py`. Any OpenAI-compatible API works: change `PROVIDER`, or use `--provider custom` and fill `LLM_BASE_URL`, `LLM_MODEL` and `LLM_API_KEY` in `.env`.
