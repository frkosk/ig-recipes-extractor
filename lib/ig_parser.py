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
                urls = list(_collection_urls(coll))
                result.append((cname, len(urls)))
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
                        for url in _collection_urls(coll):
                            yield SavedPost(url=url)
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


def _collection_name(coll: dict) -> str:
    for lv in coll.get("label_values") or []:
        if lv.get("label") == "Name":
            return lv.get("value") or "?"
    return "?"


def _collection_urls(coll: dict) -> Iterator[str]:
    """Walk the nested dict-of-dicts structure inside a collection entry to yield post URLs."""
    for lv in coll.get("label_values") or []:
        for post in _walk_for_urls(lv):
            yield post


IG_POST_URL_PREFIX = "https://www.instagram.com/"


def _walk_for_urls(node) -> Iterator[str]:
    if isinstance(node, dict):
        if node.get("label") == "URL":
            u = node.get("href") or node.get("value")
            if u and isinstance(u, str) and u.startswith(IG_POST_URL_PREFIX):
                yield u
        for v in node.values():
            yield from _walk_for_urls(v)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_for_urls(item)


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
        for lv in entry.get("label_values") or []:
            if lv.get("label") == "URL":
                href = lv.get("href") or lv.get("value")
                if href:
                    yield SavedPost(url=href)
                    break
        else:
            # Older schema: `string_map_data.Saved on.href`
            smd = entry.get("string_map_data") or {}
            saved = smd.get("Saved on") or {}
            href = saved.get("href")
            if href:
                yield SavedPost(url=href)
