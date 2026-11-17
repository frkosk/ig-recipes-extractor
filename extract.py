"""Main entrypoint: orchestrate IG → Obsidian recipe pipeline."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from lib import downloader, extractor, ig_parser, transcriber, writer


PROJECT_ROOT = Path(__file__).resolve().parent
INBOX = PROJECT_ROOT / "inbox"
WORK = PROJECT_ROOT / "work"
STATE_PATH = PROJECT_ROOT / "state.json"


def _load_state() -> dict:
    if STATE_PATH.exists():
        state = json.loads(STATE_PATH.read_text())
    else:
        state = {}
    state.setdefault("processed", {})
    state.setdefault("failed", {})
    state.setdefault("skipped", {})
    return state


def _save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def _canonicalize(url: str) -> str:
    u = url.split("?")[0].split("#")[0].rstrip("/")
    # normalize /reel/ /reels/ /p/ /tv/ to /p/ so the same shortcode isn't seen twice
    u = re.sub(r"/(?:reel|reels|tv)/", "/p/", u)
    return u


def _post_id(url: str) -> str:
    m = re.search(r"/(?:reel|p|tv)/([^/?#]+)", url)
    return m.group(1) if m else re.sub(r"[^a-zA-Z0-9]+", "_", url)[-32:]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _vault_dir() -> Path:
    env = os.environ.get("VAULT_RECIPES_DIR")
    if not env:
        raise SystemExit("VAULT_RECIPES_DIR not set (put it in .env)")
    return Path(env).expanduser()


def _process_url(url: str, state: dict, vault: Path, dry_run: bool, export_caption: str = "") -> None:
    canon = _canonicalize(url)
    pid = _post_id(canon)
    print(f"→ {canon}")

    if dry_run:
        print("  (dry-run)")
        return

    try:
        dl = downloader.download(canon, WORK, pid)
    except downloader.DownloadError as e:
        if not export_caption:
            state["failed"][canon] = {"reason": f"download: {e}", "attempted_at": _now()}
            print(f"  ✗ download failed: {e}")
            return
        # Photo/carousel posts often fail in yt-dlp; the export still has the caption.
        print(f"  ⚠ download failed, falling back to export caption ({len(export_caption)} chars)")
        dl = downloader.DownloadResult(
            video_path=None, thumbnail_path=None, caption=export_caption, uploader="",
        )
        download_error = str(e)
    else:
        download_error = ""

    transcript_text = ""
    if dl.video_path:
        try:
            t = transcriber.transcribe(dl.video_path)
            transcript_text = t.text
            print(f"  transcript: {len(transcript_text)} chars, lang={t.language}")
        except Exception as e:
            print(f"  ⚠ transcribe failed: {e} (continuing with caption only)")

    try:
        recipe = extractor.extract_recipe(
            caption=dl.caption,
            transcript=transcript_text,
            image_path=dl.thumbnail_path,
        )
    except Exception as e:
        reason = f"extract: {e}"
        if download_error:
            reason = f"download: {download_error}; {reason}"
        state["failed"][canon] = {"reason": reason, "attempted_at": _now()}
        print(f"  ✗ extract failed: {e}")
        return

    if not recipe.is_recipe and download_error:
        # Caption alone wasn't enough — keep it in the backlog, not in skipped.
        state["failed"][canon] = {
            "reason": f"download: {download_error}; caption has no recipe",
            "attempted_at": _now(),
        }
        print(f"  ✗ caption has no recipe: {recipe.name}")
        return

    if not recipe.is_recipe:
        state["skipped"][canon] = {
            "reason": "not a recipe",
            "name": recipe.name,
            "attempted_at": _now(),
        }
        state["failed"].pop(canon, None)
        print(f"  ⊘ skipped (not a recipe): {recipe.name}")
        return

    slug, md_path = writer.write_recipe(
        recipe=recipe,
        source_url=canon,
        author=f"@{dl.uploader}" if dl.uploader else "",
        thumbnail_path=dl.thumbnail_path,
        recipes_dir=vault,
    )
    state["processed"][canon] = {
        "slug": slug,
        "extracted_at": _now(),
        "vault_file": md_path.name,
        "confidence": recipe.confidence,
        "source": "export caption" if download_error else "download",
    }
    state["failed"].pop(canon, None)
    print(f"  ✓ {recipe.name} → {md_path.name} (confidence={recipe.confidence})")


def _urls_from_zip(zip_path: Path, collection: str | None) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for post in ig_parser.iter_saved_posts_from_zip(zip_path, collection=collection):
        c = _canonicalize(post.url)
        if c not in seen:
            seen.add(c)
            result.append(c)
    return result


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")

    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--zip", type=Path, help="Path to IG data export zip")
    g.add_argument("--url", type=str, help="Single URL (ad-hoc)")
    g.add_argument("--retry-failed", action="store_true")
    g.add_argument("--list-collections", action="store_true", help="Print collections in the ZIP and exit")
    ap.add_argument(
        "--collection", type=str, default="Recipes",
        help="IG saved-collection name to filter by. Use '*' to include all saved posts. Default: 'Recipes'.",
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None, help="Cap number of URLs processed")
    args = ap.parse_args()

    vault = _vault_dir()
    state = _load_state()

    if not args.dry_run:
        dash = writer.ensure_dashboard(vault)
        if dash:
            print(f"Created dashboard: {dash.name}")

    if args.list_collections:
        zip_path = args.zip or ig_parser.find_latest_export(INBOX)
        if not zip_path:
            print(f"No zip in {INBOX} and --zip not given.", file=sys.stderr)
            return 2
        print(f"Collections in {zip_path.name}:")
        for name, count in ig_parser.list_collections(zip_path):
            print(f"  {count:4}  {name}")
        return 0

    captions: dict[str, str] = {}
    if export_zip := args.zip or ig_parser.find_latest_export(INBOX):
        captions = {
            _canonicalize(u): c for u, c in ig_parser.captions_from_zip(export_zip).items()
        }

    if args.url:
        urls = [args.url]
    elif args.retry_failed:
        urls = list(state["failed"].keys())
    else:
        zip_path = args.zip or ig_parser.find_latest_export(INBOX)
        if not zip_path:
            print(f"No zip in {INBOX} and --zip not given.", file=sys.stderr)
            return 2
        print(f"Using export: {zip_path.name}")
        coll = None if args.collection == "*" else args.collection
        print(f"Collection: {coll or '(all saved)'}")
        urls = _urls_from_zip(zip_path, collection=coll)

    urls = [u for u in urls if u]
    if not args.retry_failed and not args.url:
        urls = [u for u in urls if u not in state["processed"] and u not in state["skipped"]]

    if args.limit:
        urls = urls[: args.limit]

    print(f"To process: {len(urls)} URL(s)")
    if not urls:
        return 0

    if not args.dry_run:
        provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()
        model = (
            os.environ.get("OPENROUTER_MODEL", extractor.DEFAULT_OPENROUTER_MODEL)
            if provider == "openrouter"
            else os.environ.get("ANTHROPIC_MODEL", extractor.DEFAULT_ANTHROPIC_MODEL)
        )
        print(f"LLM provider: {provider} ({model})")

    try:
        for url in urls:
            try:
                _process_url(url, state, vault, args.dry_run, captions.get(_canonicalize(url), ""))
            except KeyboardInterrupt:
                raise
            except Exception:
                traceback.print_exc()
                state["failed"][_canonicalize(url)] = {
                    "reason": "unhandled exception",
                    "attempted_at": _now(),
                }
            finally:
                if not args.dry_run:
                    _save_state(state)
    finally:
        if not args.dry_run:
            _save_state(state)
            backlog = writer.write_backlog(vault, state, captions)
            print(f"Backlog: {backlog.name}")

    p = len(state["processed"])
    s = len(state["skipped"])
    f = len(state["failed"])
    print(f"\nDone. state: {p} processed, {s} skipped (not recipes), {f} failed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
