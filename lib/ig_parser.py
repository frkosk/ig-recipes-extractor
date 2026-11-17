"""Parse Instagram data export ZIP and extract saved post URLs."""
from __future__ import annotations

import json
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass
class SavedPost:
    url: str
    caption: str = ""


def find_latest_export(inbox: Path) -> Path | None:
    zips = sorted(inbox.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    return zips[0] if zips else None


def list_collections(zip_path: Path) -> list[tuple[str, int]]:
    """Return (name, url_count) for every saved collection in the export."""
    result: list[tuple[str, int]] = []
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if not name.endswith("saved_collections.json"):
                continue
            with zf.open(name) as f:
                data = json.load(f)
            if not isinstance(data, list):
                continue
            for coll in data:
                cname = _collection_name(coll)
                posts = list(_collection_posts(coll))
                result.append((cname, len(posts)))
    return result


def iter_saved_posts_from_zip(
    zip_path: Path,
    collection: str | None = None,
) -> Iterator[SavedPost]:
    """Yield saved posts from an IG data export ZIP.

    If `collection` is given, yield URLs from that named collection only
    (matched case-insensitively against saved_collections.json). If it is
    None, yield every saved post (saved_posts.json + collections).
    """
    with zipfile.ZipFile(zip_path) as zf:
        if collection is not None:
            found = False
            for name in zf.namelist():
                if not name.endswith("saved_collections.json"):
                    continue
                with zf.open(name) as f:
                    data = json.load(f)
                if not isinstance(data, list):
                    continue
                for coll in data:
                    if _collection_name(coll).lower() == collection.lower():
                        found = True
                        yield from _collection_posts(coll)
            if not found:
                raise ValueError(f"Collection {collection!r} not found in {zip_path.name}")
            return

        candidates = [
            n for n in zf.namelist()
            if n.endswith("saved_posts.json")
        ]
        if not candidates:
            raise FileNotFoundError(
                f"No saved_posts.json in {zip_path}. Contents sample: {zf.namelist()[:10]}"
            )
        total = 0
        for name in candidates:
            with zf.open(name) as f:
                data = json.load(f)
            for post in _extract_posts(data):
                total += 1
                yield post
        if total == 0:
            print(
                f"WARNING: {zip_path.name} contained {len(candidates)} saved_posts.json files but no URLs. "
                "IG export schema may have changed.",
                file=sys.stderr,
            )


# The export localizes labels to the account language (e.g. SK "Meno").
COLLECTION_NAME_LABELS = {"Name", "Meno"}


def _fix_mojibake(s: str) -> str:
    """IG exports write UTF-8 bytes as latin-1 code points; undo that."""
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def _collection_name(coll: dict) -> str:
    for lv in coll.get("label_values") or []:
        if lv.get("label") in COLLECTION_NAME_LABELS:
            return _fix_mojibake(lv.get("value") or "?")
    return "?"


def _collection_posts(coll: dict) -> Iterator[SavedPost]:
    """Walk the nested dict-of-dicts structure inside a collection entry to yield posts."""
    for lv in coll.get("label_values") or []:
        yield from _walk_for_posts(lv)


IG_POST_URL_PREFIX = "https://www.instagram.com/"
CAPTION_LABELS = {"Caption", "Titulok"}


def _post_from_fields(fields: list) -> SavedPost | None:
    """Build a post from sibling {label, value, href} entries (URL + caption)."""
    url = caption = None
    for f in fields:
        if not isinstance(f, dict):
            continue
        if f.get("label") == "URL" and url is None:
            u = f.get("href") or f.get("value")
            if isinstance(u, str) and u.startswith(IG_POST_URL_PREFIX):
                url = u
        elif f.get("label") in CAPTION_LABELS and caption is None:
            caption = f.get("value")
    if not url:
        return None
    return SavedPost(url=url, caption=_fix_mojibake(caption) if caption else "")


def _walk_for_posts(node) -> Iterator[SavedPost]:
    if isinstance(node, list):
        post = _post_from_fields(node)
        if post:
            yield post
            return
        for item in node:
            yield from _walk_for_posts(item)
    elif isinstance(node, dict):
        for v in node.values():
            yield from _walk_for_posts(v)


def captions_from_zip(zip_path: Path) -> dict[str, str]:
    """Map post URL → caption across saved posts and all collections."""
    result: dict[str, str] = {}
    for post in iter_saved_posts_from_zip(zip_path):
        if post.caption:
            result[post.url] = post.caption
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if not name.endswith("saved_collections.json"):
                continue
            with zf.open(name) as f:
                data = json.load(f)
            if not isinstance(data, list):
                continue
            for coll in data:
                for post in _collection_posts(coll):
                    if post.caption:
                        result.setdefault(post.url, post.caption)
    return result


def _extract_posts(data: dict | list) -> Iterator[SavedPost]:
    entries: list = []
    if isinstance(data, dict):
        for key in ("saved_saved_media", "saved_media"):
            if key in data:
                entries = data[key]
                break
    else:
        entries = data
    for entry in entries:
        # Newer schema (2025+): entry has `label_values` list with objects
        # {label, value, href}. URL entry is label=="URL".
        post = _post_from_fields(entry.get("label_values") or [])
        if post:
            yield post
        else:
            # Older schema: `string_map_data.Saved on.href`
            smd = entry.get("string_map_data") or {}
            saved = smd.get("Saved on") or {}
            href = saved.get("href")
            if href:
                yield SavedPost(url=href)
