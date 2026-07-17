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


def iter_saved_posts_from_zip(zip_path: Path) -> Iterator[SavedPost]:
    with zipfile.ZipFile(zip_path) as zf:
        candidates = [
            n for n in zf.namelist()
            if n.endswith("saved_posts.json") or n.endswith("saved_collections.json")
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
                f"WARNING: {zip_path.name} contained {len(candidates)} saved_*.json files but no URLs. "
                "IG export schema may have changed.",
                file=sys.stderr,
            )


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
        smd = entry.get("string_map_data") or {}
        saved = smd.get("Saved on") or {}
        href = saved.get("href")
        if href:
            yield SavedPost(url=href)
