# ig-recipes-extractor

Pipeline: **Instagram saved reels/posts → Obsidian markdown library**.

Downloads an IG reel with `yt-dlp`, transcribes the audio locally with `mlx-whisper` (Apple Silicon), extracts a structured recipe via LLM tool use (**Anthropic API** or **OpenRouter** — swappable via env), and writes it into your Obsidian vault as `.md` + a cover image in `_attachments/`. It also auto-generates `00 Dashboard.md` with Dataview blocks.

Recipe output (name, ingredients, steps, notes) is written in Slovak — the Claude system prompt is authored in SK and instructs the model to translate non-SK sources. If you want a different output language, replace the prompt strings in `lib/extractor.py` and the section headings in `lib/writer.py`.

## Setup (one-time)

```bash
brew install ffmpeg uv
cd ~/WORK/ig-recipes-extractor
uv sync
cp .env.example .env
# edit .env: set VAULT_RECIPES_DIR and the API key for your chosen LLM_PROVIDER
```

`yt-dlp` is a Python dep (not brew) — it's installed by `uv sync`.

`VAULT_RECIPES_DIR` default:
`/Users/ferikocurik/Library/Mobile Documents/iCloud~md~obsidian/Documents/Recipies`

In Obsidian, install the **Dataview** plugin (Community plugins → Browse → Dataview → Install → Enable) so `00 Dashboard.md` renders. In Dataview settings also enable **JavaScript Queries** for the "Random recipe" block.

## LLM provider

Switch via `LLM_PROVIDER` in `.env`:

### `anthropic` (default)

Direct Anthropic API. Model: `claude-sonnet-4-6` (override via `ANTHROPIC_MODEL`).
Requires `ANTHROPIC_API_KEY`.
The system prompt uses `cache_control: ephemeral` — with sequential URLs within a 5-minute TTL window, this saves input tokens.

### `openrouter`

OpenAI-compatible endpoint at `https://openrouter.ai/api/v1`.
Requires `OPENROUTER_API_KEY` from https://openrouter.ai/keys.
Model: defaults to `anthropic/claude-sonnet-4.5` (override via `OPENROUTER_MODEL`).

Popular `OPENROUTER_MODEL` alternatives:
- `anthropic/claude-sonnet-4.5` — Claude Sonnet 4.5 via OpenRouter (good quality, reliable tool use)
- `openai/gpt-4o-mini` — cheap, fast, decent on SK
- `google/gemini-2.5-flash` — very cheap, strong image input support
- `deepseek/deepseek-chat` — cheapest, quality is inconsistent for this use case

Full model list: https://openrouter.ai/models. **The model must support `tools` (function calling)**, otherwise extraction will fail.

Optional headers for OpenRouter's app ranking (not required):
- `OPENROUTER_HTTP_REFERER`
- `OPENROUTER_APP_TITLE`

## Cookies (IG login)

IG almost always requires a session when downloading reels. Set one of these in `.env`:

```
IG_COOKIES_FROM_BROWSER=chrome   # or brave/firefox/safari/edge/opera
IG_COOKIES_FILE=cookies.txt      # relative to project root, or abs path / ~
```

Cookie file export: in Chrome/Brave/Firefox install the **Get cookies.txt LOCALLY** extension, open instagram.com, click the extension icon → Export Netscape → drop the file into the project root as `cookies.txt`.

## Manual IG data export (per batch)

1. IG app → **Settings → Accounts Center → Your information and permissions → Download your information**
2. **Some of your information → Saved → Include** (nothing else needed)
3. Format: **JSON**, Date range: **All time**
4. Wait 24–48h for the email from IG
5. Download the ZIP, drop it into `inbox/`
6. `uv run extract.py`

The script automatically:
- Picks the latest ZIP in `inbox/`
- Takes only posts from the IG saved collection **Recipes** (override with `--collection`, see below)
- Filters URLs already recorded in `state.json` (processed or skipped)
- Processes only new ones
- Writes `.md` files + `_attachments/*.jpg` into the vault
- On first run in a vault, creates `00 Dashboard.md`

## CLI

```bash
uv run extract.py                          # latest ZIP in inbox/
uv run extract.py --zip path/to/export.zip # specific ZIP
uv run extract.py --url https://www.instagram.com/reel/XYZ/  # single URL (for testing)
uv run extract.py --retry-failed           # retry every URL in state.failed
uv run extract.py --dry-run                # list URLs, write nothing
uv run extract.py --limit 3                # cap to first N URLs
uv run extract.py --list-collections       # print saved collections (with post counts) in the ZIP and exit
uv run extract.py --collection "Dinner"    # use a different saved collection (case-insensitive)
uv run extract.py --collection '*'         # all saved posts, regardless of collection
```

`--collection` defaults to `Recipes`. If the named collection doesn't exist in the export, the run aborts — use `--list-collections` to see what's there. `--url` and `--retry-failed` ignore the collection filter and the dedup ledger.

## Photo / carousel posts

yt-dlp can't download posts without video (`No video formats found!`). In that case the pipeline falls back to the caption from the IG export (`Titulok`/`Caption`) and extracts the recipe from text only (no transcript, no cover image). If the caption doesn't contain a recipe, the post goes to the backlog.

## Backlog (`00 Backlog.md`)

Regenerated in the vault after every non-dry run from `state.json`:
- **Nespracované** — everything in `failed`, with the reason and a caption excerpt
- **Na kontrolu** — processed recipes with `confidence: low`

Manual edits are overwritten.

## Non-recipe posts

The LLM first decides whether the post is actually a recipe (`is_recipe`). Non-recipe content (lifestyle, memes, travel, ads, …) is not written to the vault; it's recorded under `skipped` in `state.json` and won't be retried on later runs.

## Output layout

```
<VAULT_RECIPES_DIR>/
├── 00 Dashboard.md               # auto-generated, Dataview
├── Belgická cibuľová omáčka.md
├── Pumpkin Cookies.md
└── _attachments/                 # hidden in Obsidian sidebar (`_` prefix)
    ├── Belgická cibuľová omáčka.jpg
    └── ...
```

Filenames use the recipe title as-is (unicode + spaces preserved); only `/`, `\`, `:`, and control chars are sanitized to `-`. The `_attachments/` folder is hidden in Obsidian via the `_`-prefix filter.

### Recipe note frontmatter

```yaml
name: "Belgická cibuľová omáčka"
aliases: ["Belgian sauce", "Omáčka z benzínky"]
source_url: "https://www.instagram.com/p/DanEuF6sY2_"
source_author: "@Jorito"
cuisine: belgicka
meal_type: ["omacka"]
difficulty: medium
main_ingredient: "Cibuľa"
tags: ["kuchyna/belgicka", "chod/omacka", "narocnost/medium", "ingr/cibula", "styl/vegetarian"]
servings: 4                       # optional
prep_time_minutes: 10             # optional, numeric
cook_time_minutes: 30             # optional, numeric
total_time_minutes: 40            # optional, numeric (auto-computed)
confidence: high                  # high | medium | low
extracted: 2026-07-17
```

Nested tags are derived from the structured fields:
- `kuchyna/{cuisine}`
- `chod/{meal_type[i]}` (one per meal_type entry)
- `narocnost/{difficulty}`
- `ingr/{main_ingredient}` (slugged)
- `styl/{tag}` — for free-form style tags (rychle, vegetarian, …)

Recipes with `confidence: low` also get a `kontrola-potrebna` tag.

The body has sections **Ingrediencie / Postup / Poznámky / Podobné**, with an auto-wikilink on `main_ingredient` both in the header and in ingredient list items (word-boundary aware for declension — e.g. `cesnaku` links as `[[Cesnak|cesnaku]]`).

## Dashboard (`00 Dashboard.md`)

Created once on the first non-dry-run invocation. Contains Dataview blocks:
- Quick recipes (≤ 30 min)
- By cuisine
- By main ingredient
- By course
- Needs review (confidence low)
- Random recipe (`dataviewjs` — requires Enable JavaScript Queries)

Queries use `WHERE file.folder = this.file.folder` so they work regardless of where the vault root is.

## State (`state.json`)

Dedup ledger. Git-ignored (each machine keeps its own).

```json
{
  "processed": {
    "https://www.instagram.com/p/XYZ": {
      "slug": "Recipe",
      "extracted_at": "2026-07-17T11:55:44+00:00",
      "vault_file": "Recipe.md",
      "confidence": "high",
      "source": "download"            // or "export caption" (fallback)
    }
  },
  "failed": {
    "https://www.instagram.com/reel/ABC": {
      "reason": "download: yt-dlp failed (1): ...",
      "attempted_at": "2026-07-17T12:01:22+00:00"
    }
  },
  "skipped": {
    "https://www.instagram.com/p/DEF": {
      "reason": "not a recipe",
      "name": "Cestovateľské video z Talianska",
      "attempted_at": "2026-07-17T12:03:10+00:00"
    }
  }
}
```

Failed URLs can be retried with `--retry-failed`. A successful retry moves the entry from `failed` to `processed` (or `skipped`). URLs in `skipped` are never reprocessed automatically; delete the entry from `state.json` (or use `--url`) to force a re-run.

URLs are canonicalized before being stored: query string and trailing slash are dropped, and `/reel/`, `/reels/`, `/tv/` are rewritten to `/p/`, so the same post saved via different links is processed once.

## Project layout

```
extract.py               # CLI entrypoint
lib/
├── ig_parser.py         # parse IG data export ZIP
├── downloader.py        # yt-dlp wrapper (+ cookies handling)
├── transcriber.py       # ffmpeg → mlx-whisper
├── extractor.py         # Anthropic/OpenRouter tool-use extraction
└── writer.py            # md rendering, dashboard bootstrap
inbox/                   # user drops the IG export ZIP here (gitignored)
work/                    # temp video/audio/transcript (gitignored)
state.json               # dedup state (gitignored)
.env                     # LLM key, vault path, cookies (gitignored)
cookies.txt              # IG session (gitignored)
```

## Troubleshooting

- **`yt-dlp: Instagram sent an empty media response`** → IG wants a login. Set `IG_COOKIES_FROM_BROWSER=chrome` or `IG_COOKIES_FILE=cookies.txt` in `.env`. With `--cookies-from-browser chrome` Chrome must be closed (SQLite lock on cookie DB).
- **`yt-dlp timed out after 300s`** → slow network or IG throttling. Try again via `--retry-failed`.
- **`yt-dlp: private post`** → the post went private/deleted after you saved it. `--retry-failed` won't help.
- **`No module named expat`** → brew's yt-dlp build is broken. We use the pip yt-dlp installed via `uv`; run `uv sync` to make sure the venv is populated.
- **Whisper downloads ~1.5GB on first run** — normal, model cache lives in `~/.cache/huggingface/`.
- **`VAULT_RECIPES_DIR not set`** → add it to `.env`.
- **OpenRouter: `no save_recipe tool call`** → the chosen model doesn't support tool/function calling, or refused to return one. Switch to Claude / GPT-4o / Gemini 2.5.
- **Dashboard shows "No results to show"** → Dataview plugin isn't enabled, or trigger `Cmd+P` → **Dataview: Force refresh all views**.
- **Many recipes come out with low confidence** → the source only has the recipe in a pinned comment or on-screen text. Thumbnails are already sent as vision input, but sometimes there's nothing useful there.
