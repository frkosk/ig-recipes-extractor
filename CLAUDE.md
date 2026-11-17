# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Single-user CLI pipeline: Instagram data-export ZIP (saved posts) → `yt-dlp` download → local `mlx-whisper` transcription (Apple Silicon only) → LLM tool-use extraction → Obsidian markdown notes in `VAULT_RECIPES_DIR`. See README.md for user-facing setup (cookies, IG export, Dataview).

## Commands

Python ≥3.12, managed by `uv`. Requires `ffmpeg` on PATH. No test suite, linter, or build step exists.

```bash
uv sync                                         # install deps (yt-dlp comes from here, not brew)
uv run extract.py --dry-run                     # list URLs that would be processed; writes nothing (no state, no dashboard)
uv run extract.py --list-collections            # show saved collections in latest inbox/ ZIP
uv run extract.py --collection '*'              # all saved posts (default collection is "Recipes")
uv run extract.py --url https://www.instagram.com/reel/XYZ/   # single URL — the way to test end-to-end
uv run extract.py --retry-failed
uv run extract.py --limit 3
```

`--url` and `--retry-failed` bypass the processed/skipped dedup filter. Any non-dry run hits the real vault, real LLM API, and mutates `state.json` — use `--limit`/`--url` when testing changes.

## Architecture

`extract.py` orchestrates; each `lib/` module is one pipeline stage with no cross-dependencies except `writer` importing `RecipeExtract` from `extractor`.

- **URL identity**: `_canonicalize()` in `extract.py` strips query/fragment/trailing slash and rewrites `/reel/`, `/reels/`, `/tv/` → `/p/`. Canonical URLs are the keys in `state.json`; any new URL entry point must go through it or dedup breaks.
- **State** (`state.json`, gitignored): three buckets — `processed`, `failed`, `skipped` (LLM said `is_recipe: false`). Saved after every URL so Ctrl-C is safe. Success/skip removes the URL from `failed`.
- **ig_parser**: handles two IG export schemas — newer `label_values` (2025+) and older `string_map_data["Saved on"]`. Collection filtering reads `saved_collections.json` and walks nested dicts for `label == "URL"`; raises if the named collection is missing.
- **downloader**: shells out to `python -m yt_dlp` (venv's yt-dlp) into `work/<post_id>/`; cookies via `IG_COOKIES_FROM_BROWSER` or `IG_COOKIES_FILE`. Transcription failure is non-fatal (falls back to caption only).
- **extractor**: one forced tool call `save_recipe` with `RECIPE_SCHEMA`; caption + transcript as text, thumbnail as image input. Two backends selected by `LLM_PROVIDER` (`anthropic` default, `openrouter` via OpenAI SDK) — schema/prompt changes must work for both `_extract_anthropic` and `_extract_openrouter`. Anthropic path uses prompt caching on the system prompt.
- **writer**: renders frontmatter + nested tags (`kuchyna/`, `chod/`, `narocnost/`, `ingr/`, `styl/`, plus `kontrola-potrebna` for low confidence) derived from structured fields, auto-wikilinks `main_ingredient`, copies thumbnail to `_attachments/<title>.jpg`. Filename = recipe title (only `/ \ :` and control chars sanitized). `ensure_dashboard` writes `00 Dashboard.md` only if absent — editing `DASHBOARD_CONTENT` won't update existing vaults.

## Conventions

- Output language is Slovak: the system prompt in `lib/extractor.py` and section headings in `lib/writer.py` are in Slovak with full diacritics. Categorization values (`cuisine`, `meal_type`, `tags`) are lowercase without diacritics because they become Obsidian tag paths; `main_ingredient` keeps diacritics and is Capitalized.
- Adding a field means touching `RECIPE_SCHEMA`, the `RecipeExtract` dataclass, `_to_recipe`, the system prompt, and `writer._render` (and possibly the dashboard queries).
- README.md documents CLI flags and the `state.json` format — update it when changing either.
