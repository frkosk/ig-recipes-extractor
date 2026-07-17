# ig-recipes-extractor

Pipeline: **Instagram saved reels/posts → Obsidian markdown knižnica**.

Stiahne IG reel cez `yt-dlp`, prepíše audio cez `mlx-whisper` (lokálne na Mac), extrahuje štruktúrovaný recept cez LLM tool use (**Anthropic API** alebo **OpenRouter** — prepínateľné cez env) a zapíše do Obsidian vaultu ako `.md` + cover obrázok v `_attachments/`. Automaticky vygeneruje aj `00 Dashboard.md` s Dataview blokmi.

## Setup (one-time)

```bash
brew install ffmpeg uv
cd ~/WORK/ig-recipes-extractor
uv sync
cp .env.example .env
# uprav .env: nastav VAULT_RECIPES_DIR a kľúč pre zvolený LLM_PROVIDER
```

`yt-dlp` je Python dep (nie brew) — inštaluje sa cez `uv sync`.

`VAULT_RECIPES_DIR` default:
`/Users/ferikocurik/Library/Mobile Documents/iCloud~md~obsidian/Documents/Recipies`

V Obsidiane si nainštaluj plugin **Dataview** (Community plugins → Browse → Dataview → Install → Enable), aby `00 Dashboard.md` fungoval. V settings Dataview zapni aj **Enable JavaScript Queries** pre sekciu "Náhodný recept".

## LLM provider

Prepínač `LLM_PROVIDER` v `.env`:

### `anthropic` (default)

Priamy Anthropic API. Model: `claude-sonnet-4-6` (override cez `ANTHROPIC_MODEL`).
Vyžaduje `ANTHROPIC_API_KEY`.
System prompt používa `cache_control: ephemeral` — pri sekvenčnom behu v 5-min TTL okne šetrí input tokens.

### `openrouter`

OpenAI-compatible endpoint na `https://openrouter.ai/api/v1`.
Vyžaduje `OPENROUTER_API_KEY` z https://openrouter.ai/keys.
Model: default `anthropic/claude-sonnet-4.5` (override cez `OPENROUTER_MODEL`).

Populárne alternatívy pre `OPENROUTER_MODEL`:
- `anthropic/claude-sonnet-4.5` — Claude Sonnet 4.5 cez OpenRouter (dobrá kvalita, tool use)
- `openai/gpt-4o-mini` — lacný, rýchly, decentný na SK
- `google/gemini-2.5-flash` — veľmi lacný, dobrý pri image inputs
- `deepseek/deepseek-chat` — najlacnejší, kvalita na tomto usecase kolíše

Zoznam všetkých modelov: https://openrouter.ai/models. **Model musí podporovať `tools` (function calling)**, inak extrakcia zlyhá.

Voliteľné hlavičky pre OpenRouter ranking (nie sú povinné):
- `OPENROUTER_HTTP_REFERER`
- `OPENROUTER_APP_TITLE`

## Cookies (IG login)

IG dnes takmer vždy žiada session pri s­ťahovaní reels. Nastav v `.env` jedno z:

```
IG_COOKIES_FROM_BROWSER=chrome   # alebo brave/firefox/safari/edge/opera
IG_COOKIES_FILE=cookies.txt      # relatívne k project rootu, alebo abs. cesta / ~
```

Export cookies do súboru: v Chrome/Brave/Firefox nainštaluj extension **Get cookies.txt LOCALLY**, na instagram.com klikni ikonu → Export Netscape → hoď do project rootu ako `cookies.txt`.

## Manual IG data export (každý batch)

1. IG appka → **Settings → Accounts Center → Your information and permissions → Download your information**
2. **Some of your information → Saved → Include** (nič iné netreba)
3. Format: **JSON**, Date range: **All time**
4. Počkať 24–48h na email od IG
5. Stiahnuť ZIP, hodiť do `inbox/`
6. `uv run extract.py`

Skript automaticky:
- vezme najnovší ZIP z `inbox/`
- vyfiltruje URL, ktoré už v `state.json` sú spracované
- spracuje len prírastky
- zapíše `.md` súbory + `_attachments/*.jpg` do vaultu
- pri prvom behu vo vaulte vytvorí `00 Dashboard.md`

## CLI

```bash
uv run extract.py                          # najnovší ZIP z inbox/
uv run extract.py --zip path/to/export.zip # konkrétny ZIP
uv run extract.py --url https://www.instagram.com/reel/XYZ/  # jeden URL (test)
uv run extract.py --retry-failed           # retry všetkých z state.failed
uv run extract.py --dry-run                # ukáž URL, nič nezapisuj
uv run extract.py --limit 3                # obmedzenie na prvé 3 URL
```

## Štruktúra výstupu

```
<VAULT_RECIPES_DIR>/
├── 00 Dashboard.md               # auto-generated, Dataview
├── Belgická cibuľová omáčka.md
├── Pumpkin Cookies.md
└── _attachments/                 # skryté v Obsidian sidebar (`_` prefix)
    ├── Belgická cibuľová omáčka.jpg
    └── ...
```

Súbory sú pomenované ľudsky (unicode + medzery), sanitized len `/`, `\`, `:`, control chars. Attachments folder `_attachments/` je skrytý cez Obsidian filter na `_`-prefix.

### Frontmatter recept-noty

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

Nested tagy sú stavané zo štruktúrovaných polí:
- `kuchyna/{cuisine}`
- `chod/{meal_type[i]}` (jeden per meal_type entry)
- `narocnost/{difficulty}`
- `ingr/{main_ingredient}` (slug)
- `styl/{tag}` — pre voľné štýlové tagy (rychle, vegetarian, ...)

Recepty s `confidence: low` dostanú navyše tag `kontrola-potrebna`.

Telo obsahuje sekcie **Ingrediencie / Postup / Poznámky / Podobné**, s auto-wikilinkom na `main_ingredient` v hlavičke aj v položkách ingrediencií (word-boundary aware pre deklináciu — `cesnaku` linkuje na `[[Cesnak|cesnaku]]`).

## Dashboard (`00 Dashboard.md`)

Vytvorí sa raz pri prvom non-dry-run behu. Obsahuje Dataview bloky:
- Rýchle recepty (≤ 30 min)
- Podľa kuchyne
- Podľa hlavnej ingrediencie
- Podľa chodu
- Potrebuje kontrolu (confidence low)
- Náhodný recept (`dataviewjs` — vyžaduje Enable JavaScript Queries)

Queries používajú `WHERE file.folder = this.file.folder` → nezávisia od toho kde je vault root.

## State (`state.json`)

Dedup záznam. Git-trackovaný.

```json
{
  "processed": {
    "https://www.instagram.com/p/XYZ": {
      "slug": "Recept",
      "extracted_at": "2026-07-17T11:55:44+00:00",
      "vault_file": "Recept.md",
      "confidence": "high"
    }
  },
  "failed": {
    "https://www.instagram.com/reel/ABC": {
      "reason": "download: yt-dlp failed (1): ...",
      "attempted_at": "2026-07-17T12:01:22+00:00"
    }
  }
}
```

Failed URL môžeš skúsiť znova cez `--retry-failed`. Pri úspechu sa presunie z `failed` do `processed`.

## Súborová štruktúra projektu

```
extract.py               # CLI entrypoint
lib/
├── ig_parser.py         # parse IG data export ZIP
├── downloader.py        # yt-dlp wrapper (+ cookies handling)
├── transcriber.py       # ffmpeg → mlx-whisper
├── extractor.py         # Anthropic/OpenRouter tool-use extraction
└── writer.py            # md rendering, dashboard bootstrap
inbox/                   # user drops IG export ZIP here (gitignored)
work/                    # temp video/audio/transcript (gitignored)
state.json               # dedup state (tracked)
.env                     # LLM key, vault path, cookies (gitignored)
cookies.txt              # IG session (gitignored)
```

## Troubleshooting

- **`yt-dlp: Instagram sent an empty media response`** → IG žiada login. Nastav `IG_COOKIES_FROM_BROWSER=chrome` alebo `IG_COOKIES_FILE=cookies.txt` v `.env`. Pri `--cookies-from-browser chrome` musí byť Chrome zavretý (SQLite lock).
- **`yt-dlp timed out after 300s`** → sieť pomalá alebo IG throttluje. Znova cez `--retry-failed`.
- **`yt-dlp: private post`** → post medzitým prešiel na private/deleted. `--retry-failed` nezachráni.
- **`No module named expat`** → brew yt-dlp broken build. My používame pip yt-dlp cez `uv` — nemal by byť problém, ale ak áno `uv sync` znova.
- **Whisper stiahne ~1.5GB model pri prvom behu** — normal, cache v `~/.cache/huggingface/`.
- **`VAULT_RECIPES_DIR not set`** → doplň do `.env`.
- **OpenRouter: `no save_recipe tool call`** → zvolený model nemá tool/function calling support alebo ho odmietol vrátiť. Prepni model na Claude/GPT-4o/Gemini 2.5.
- **Dashboard prázdny "No results to show"** → Dataview plugin nie je enabled, alebo `Cmd+P` → **Dataview: Force refresh all views**.
- **Confidence low u viacerých** → zdroj má recept len v pinned komentári / obrázku. Thumbnail image sa už posiela do Claude ako vision input, ale niekedy tam nič nie je.
