"""Write recipe to Obsidian vault: md + attachment image + dashboard bootstrap."""
from __future__ import annotations

import re
import shutil
from datetime import date
from pathlib import Path

from slugify import slugify

from .extractor import RecipeExtract


def _slug(text: str) -> str:
    return slugify(text, lowercase=True, separator="-") or "x"


def _source_label(url: str) -> str:
    if "instagram.com" in url:
        return "Instagram"
    if "youtube.com" in url or "youtu.be" in url:
        return "YouTube"
    if "tiktok.com" in url:
        return "TikTok"
    m = re.search(r"https?://(?:www\.)?([^/]+)", url)
    return m.group(1) if m else "zdroj"


def _yaml_scalar(value: str) -> str:
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def _yaml_list(items: list[str]) -> str:
    return "[" + ", ".join(_yaml_scalar(i) for i in items) + "]"


_FS_INVALID = re.compile(r'[/\\:\x00-\x1f]')


def _sanitize_filename(name: str) -> str:
    cleaned = _FS_INVALID.sub("-", name).strip(" .")
    return cleaned or "recept"


def _pick_filename(base: str, recipes_dir: Path) -> str:
    name = _sanitize_filename(base)
    candidate = name
    n = 2
    while (recipes_dir / f"{candidate}.md").exists():
        candidate = f"{name} {n}"
        n += 1
    return candidate


def _build_tags(recipe: RecipeExtract) -> list[str]:
    tags: list[str] = []
    if recipe.cuisine:
        tags.append(f"kuchyna/{_slug(recipe.cuisine)}")
    for mt in recipe.meal_type:
        tags.append(f"chod/{_slug(mt)}")
    if recipe.difficulty:
        tags.append(f"narocnost/{_slug(recipe.difficulty)}")
    if recipe.main_ingredient:
        tags.append(f"ingr/{_slug(recipe.main_ingredient)}")
    for t in recipe.tags:
        if "/" in t:
            tags.append(t)
        else:
            tags.append(f"styl/{_slug(t)}")
    if recipe.confidence == "low":
        tags.append("kontrola-potrebna")
    seen = set()
    dedup = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            dedup.append(t)
    return dedup


def _linkify_main(item: str, main: str | None) -> str:
    if not main:
        return item
    pattern = re.compile(rf"(?<!\w){re.escape(main)}\w*", re.IGNORECASE)
    m = pattern.search(item)
    if not m:
        return item
    matched = m.group(0)
    return item[: m.start()] + f"[[{main}|{matched}]]" + item[m.end():]


def _total_minutes(recipe: RecipeExtract) -> int | None:
    a, b = recipe.prep_time_minutes, recipe.cook_time_minutes
    if a is None and b is None:
        return None
    return (a or 0) + (b or 0)


def _header_line(recipe: RecipeExtract) -> str:
    parts: list[str] = []
    if recipe.main_ingredient:
        parts.append(f"**Hlavná ingrediencia:** [[{recipe.main_ingredient}]]")
    if recipe.cuisine:
        parts.append(f"**Kuchyňa:** {recipe.cuisine}")
    if recipe.difficulty:
        parts.append(f"**Náročnosť:** {recipe.difficulty}")
    return " · ".join(parts)


def _render(
    recipe: RecipeExtract,
    filename: str,
    source_url: str,
    author: str,
    has_image: bool,
) -> str:
    lines = ["---"]
    lines.append(f"name: {_yaml_scalar(recipe.name)}")
    if recipe.aliases:
        lines.append(f"aliases: {_yaml_list(recipe.aliases)}")
    lines.append(f"source_url: {_yaml_scalar(source_url)}")
    if author:
        lines.append(f"source_author: {_yaml_scalar(author)}")
    if recipe.cuisine:
        lines.append(f"cuisine: {recipe.cuisine}")
    if recipe.meal_type:
        lines.append(f"meal_type: {_yaml_list(recipe.meal_type)}")
    if recipe.difficulty:
        lines.append(f"difficulty: {recipe.difficulty}")
    if recipe.main_ingredient:
        lines.append(f"main_ingredient: {_yaml_scalar(recipe.main_ingredient)}")
    lines.append(f"tags: {_yaml_list(_build_tags(recipe))}")
    if recipe.servings is not None:
        lines.append(f"servings: {recipe.servings}")
    if recipe.prep_time_minutes is not None:
        lines.append(f"prep_time_minutes: {recipe.prep_time_minutes}")
    if recipe.cook_time_minutes is not None:
        lines.append(f"cook_time_minutes: {recipe.cook_time_minutes}")
    total = _total_minutes(recipe)
    if total is not None:
        lines.append(f"total_time_minutes: {total}")
    lines.append(f"confidence: {recipe.confidence}")
    lines.append(f"extracted: {date.today().isoformat()}")
    lines.append("---")
    lines.append("")

    if has_image:
        lines.append(f"![[_attachments/{filename}.jpg]]")
        lines.append("")

    header = _header_line(recipe)
    if header:
        lines.append(header)
        lines.append("")

    lines.append("## Ingrediencie")
    for ing in recipe.ingredients:
        lines.append(f"- {_linkify_main(ing, recipe.main_ingredient)}")
    lines.append("")

    lines.append("## Postup")
    for i, step in enumerate(recipe.steps, 1):
        lines.append(f"{i}. {step}")
    lines.append("")

    if recipe.notes:
        lines.append("## Poznámky")
        lines.append("> [!tip] Tip")
        for nline in recipe.notes.splitlines() or [recipe.notes]:
            lines.append(f"> {nline}" if nline else ">")
        lines.append("")

    lines.append("## Podobné")
    lines.append("- ")
    lines.append("")

    if source_url or author:
        lines.append("---")
        author_part = f" od {author}" if author else ""
        if source_url:
            label = _source_label(source_url)
            lines.append(f"*Zdroj: [{label}]({source_url}){author_part}*")
        else:
            lines.append(f"*Zdroj:{author_part}*")
        lines.append("")

    return "\n".join(lines)


DASHBOARD_FILENAME = "00 Dashboard.md"

DASHBOARD_CONTENT = """# Recipies Dashboard

> Vygenerované `ig-recipes-extractor`. Vyžaduje plugin **Dataview**.

## Rýchle recepty (≤ 30 min)

```dataview
TABLE total_time_minutes AS "Min", cuisine, main_ingredient
WHERE file.folder = this.file.folder AND file.name != this.file.name AND total_time_minutes <= 30
SORT total_time_minutes ASC
```

## Podľa kuchyne

```dataview
TABLE length(rows) AS "Počet", rows.file.link AS "Recepty"
WHERE file.folder = this.file.folder AND file.name != this.file.name AND cuisine
GROUP BY cuisine
SORT length(rows) DESC
```

## Podľa hlavnej ingrediencie

```dataview
TABLE length(rows) AS "Počet", rows.file.link AS "Recepty"
WHERE file.folder = this.file.folder AND file.name != this.file.name AND main_ingredient
GROUP BY main_ingredient
SORT length(rows) DESC
```

## Potrebuje kontrolu

```dataview
LIST confidence
WHERE file.folder = this.file.folder AND file.name != this.file.name AND (confidence = "low" OR contains(tags, "kontrola-potrebna"))
```

## Náhodný recept

```dataviewjs
const cur = dv.current().file;
const pages = dv.pages().where(p => p.file.folder === cur.folder && p.file.name !== cur.name).array();
if (pages.length) {
  const pick = pages[Math.floor(Math.random() * pages.length)];
  dv.paragraph("👉 " + pick.file.link);
} else {
  dv.paragraph("(žiadne recepty zatiaľ)");
}
```

## Podľa chodu

```dataview
TABLE rows.file.link AS "Recepty"
WHERE file.folder = this.file.folder AND file.name != this.file.name AND meal_type
FLATTEN meal_type AS m
GROUP BY m
SORT length(rows) DESC
```

## Všetky recepty (debug)

```dataview
LIST
WHERE file.folder = this.file.folder AND file.name != this.file.name
```
"""


def ensure_dashboard(recipes_dir: Path) -> Path | None:
    recipes_dir.mkdir(parents=True, exist_ok=True)
    dash = recipes_dir / DASHBOARD_FILENAME
    if dash.exists():
        return None
    dash.write_text(DASHBOARD_CONTENT, encoding="utf-8")
    return dash


def write_recipe(
    recipe: RecipeExtract,
    source_url: str,
    author: str,
    thumbnail_path: Path | None,
    recipes_dir: Path,
) -> tuple[str, Path]:
    recipes_dir.mkdir(parents=True, exist_ok=True)
    attachments = recipes_dir / "_attachments"
    attachments.mkdir(exist_ok=True)

    filename = _pick_filename(recipe.name, recipes_dir)
    has_image = thumbnail_path is not None and thumbnail_path.exists()

    md = _render(recipe, filename, source_url, author, has_image)
    md_path = recipes_dir / f"{filename}.md"
    md_path.write_text(md, encoding="utf-8")

    if has_image:
        shutil.copy2(thumbnail_path, attachments / f"{filename}.jpg")

    return filename, md_path


BACKLOG_FILENAME = "00 Backlog.md"


def _excerpt(text: str, limit: int = 160) -> str:
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= limit else one_line[: limit - 1] + "…"


# Captions are third-party text: neutralize Markdown/HTML/Obsidian syntax
# (`$=` inline Dataview JS, [[links]], ![[embeds]], <html>, %%comments%%).
_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>!|#$%=~{}])")


def _escape_md(text: str) -> str:
    return _MD_SPECIAL.sub(r"\\\1", text)


def write_backlog(recipes_dir: Path, state: dict, captions: dict[str, str]) -> Path:
    """Regenerate the backlog note from state: failed posts + low-confidence recipes."""
    recipes_dir.mkdir(parents=True, exist_ok=True)
    failed = sorted(state.get("failed", {}).items(), key=lambda kv: kv[1].get("attempted_at", ""), reverse=True)
    low = sorted(
        (kv for kv in state.get("processed", {}).items() if kv[1].get("confidence") == "low"),
        key=lambda kv: kv[1].get("extracted_at", ""),
        reverse=True,
    )

    lines = [
        "# Backlog",
        "",
        f"*Generované automaticky ({date.today().isoformat()}) zo `state.json` — ručné úpravy sa prepíšu.*",
        "",
        f"## Nespracované ({len(failed)})",
        "",
    ]
    for url, info in failed:
        lines.append(f"- [{url.rstrip('/').rsplit('/', 1)[-1]}]({url}) — {info.get('attempted_at', '')[:10]}")
        reason = re.sub(r"; please report this issue.*?(?=; caption has no recipe|$)", "", info.get("reason", ""), flags=re.S)
        lines.append(f"    - Dôvod: `{_excerpt(reason, 200)}`")
        if caption := captions.get(url):
            lines.append(f"    - Popis: {_escape_md(_excerpt(caption))}")
    lines += ["", f"## Na kontrolu — nízka istota ({len(low)})", ""]
    for url, info in low:
        note = Path(info.get("vault_file", "")).stem
        lines.append(f"- [[{note}]] — [Instagram]({url})")
    lines.append("")

    path = recipes_dir / BACKLOG_FILENAME
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
