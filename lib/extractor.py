"""Recipe extraction via LLM tool use.

Supports two providers, chosen by env `LLM_PROVIDER`:
- `anthropic` (default): native Anthropic SDK, uses prompt caching.
- `openrouter`: OpenAI SDK against https://openrouter.ai/api/v1.
"""
from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SYSTEM_PROMPT = """Si asistent na extrakciu receptov z Instagram reels a postov.

Vstup: caption z postu + (voliteľne) transkript hovoru z videa + (voliteľne) obrázok jedla.

Úloha: rozhodni či vstup je **recept na jedlo/nápoj** a zavolaj tool `save_recipe`.

Ak vstup NIE JE recept (napr. lifestyle video, fashion, meme, cestovanie, humor, motivačný obsah, tutorial nekulinárny, unboxing, žart, reklama, dokumentárny obsah bez ingrediencií a krokov), vráť:
- `is_recipe: false`
- `name`: krátky opis obsahu (napr. "Cestovateľské video z Talianska")
- `confidence: low`
- ostatné polia môžu byť prázdne alebo null

Ak vstup JE recept, vráť `is_recipe: true` a normálne vyplň všetky polia podľa nasledujúcich pravidiel.

Pravidlá výstupu:
- Všetko po slovensky. Ak je zdroj v inom jazyku, prelož názov, ingrediencie, kroky.
- Ingrediencie: s množstvom a jednotkou kde je uvedené ("2 PL olivový olej", "300 g múky").
- Kroky: imperatív, jedna veta = jeden krok.
- Ak niečo chýba, daj `null` a znížiť confidence, nevymýšľaj.

Kategorizačné polia (dôležité pre Obsidian tag hierarchiu):
- `cuisine`: národná kuchyňa lowercase bez diakritiky — napr. `slovenska`, `talianska`, `azijska`, `mexicka`, `francuzska`, `americka`, `indicka`, `blizkovychodna`. `null` ak nejasné.
- `meal_type`: pole hodnôt z {`ranajky`, `obed`, `vecera`, `dezert`, `snack`, `pitie`, `pridloha`, `omacka`, `pecivo`}. Aspoň jeden.
- `difficulty`: `easy` (do 30 min a menej ako 6 ingrediencií), `medium`, alebo `hard`.
- `main_ingredient`: dominantná ingrediencia jedným slovom, prvé písmeno veľké, s diakritikou — napr. `Cesnak`, `Kura`, `Ryža`, `Cuketa`. `null` len ak recept naozaj nemá dominant.
- `aliases`: alternatívne názvy (SK aj EN), max 3. Ak recept nemá známu alt názov, prázdne pole.
- `tags`: doplňujúce "štýlové" tagy lowercase bez diakritiky (napr. `vegetarian`, `veganske`, `bezmasy`, `rychle`, `bezlaktozy`, `bezlepkove`, `sviatocne`, `fitness`, `grilovanie`). Max 5. NEDÁVAJ sem cuisine/meal_type/main_ingredient — tie idú do vlastných polí.

Confidence:
- `high`: ingrediencie s množstvami + jasné kroky
- `medium`: chýbajú niektoré množstvá alebo detaily
- `low`: len inšpirácia, väčšina detailov chýba
"""


RECIPE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "is_recipe": {
            "type": "boolean",
            "description": "True ak vstup je skutočne recept na jedlo/nápoj; false ak je to čokoľvek iné.",
        },
        "name": {"type": "string", "description": "Názov receptu po slovensky (alebo krátky opis obsahu ak is_recipe=false)."},
        "aliases": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Alternatívne názvy (SK aj EN), max 3.",
        },
        "cuisine": {
            "type": ["string", "null"],
            "description": "Národná kuchyňa lowercase bez diakritiky, alebo null.",
        },
        "meal_type": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Aspoň jeden z: ranajky/obed/vecera/dezert/snack/pitie/pridloha/omacka/pecivo.",
        },
        "difficulty": {
            "type": ["string", "null"],
            "enum": ["easy", "medium", "hard"],
        },
        "main_ingredient": {
            "type": ["string", "null"],
            "description": "Dominantná ingrediencia jedným slovom, Capitalized, s diakritikou.",
        },
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Doplňujúce štýlové tagy lowercase bez diakritiky. Max 5.",
        },
        "servings": {"type": ["integer", "null"]},
        "prep_time_minutes": {"type": ["integer", "null"]},
        "cook_time_minutes": {"type": ["integer", "null"]},
        "ingredients": {"type": "array", "items": {"type": "string"}},
        "steps": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": ["string", "null"]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["is_recipe", "name", "meal_type", "tags", "ingredients", "steps", "confidence"],
}

TOOL_NAME = "save_recipe"
TOOL_DESCRIPTION = "Ulož extrahovaný recept do štruktúrovanej podoby."


DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
DEFAULT_OPENROUTER_MODEL = "anthropic/claude-sonnet-4.5"


@dataclass
class RecipeExtract:
    name: str
    tags: list[str]
    ingredients: list[str]
    steps: list[str]
    confidence: str
    is_recipe: bool = True
    aliases: list[str] = field(default_factory=list)
    cuisine: str | None = None
    meal_type: list[str] = field(default_factory=list)
    difficulty: str | None = None
    main_ingredient: str | None = None
    servings: int | None = None
    prep_time_minutes: int | None = None
    cook_time_minutes: int | None = None
    notes: str | None = None


class ExtractError(RuntimeError):
    pass


def _image_bytes(image_path: Path | None) -> tuple[str, str] | None:
    if not image_path or not image_path.exists():
        return None
    suffix = image_path.suffix.lower().lstrip(".")
    media_type = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(suffix)
    if not media_type:
        return None
    data = base64.standard_b64encode(image_path.read_bytes()).decode("ascii")
    return media_type, data


def _user_text(caption: str, transcript: str) -> str:
    return (
        f"=== IG CAPTION ===\n{caption or '(prázdne)'}\n\n"
        f"=== TRANSKRIPT AUDIA ===\n{transcript or '(prázdne)'}"
    )


def _to_recipe(payload: dict) -> RecipeExtract:
    return RecipeExtract(
        name=payload["name"],
        tags=payload.get("tags", []) or [],
        ingredients=payload["ingredients"],
        steps=payload["steps"],
        confidence=payload["confidence"],
        is_recipe=bool(payload.get("is_recipe", True)),
        aliases=payload.get("aliases", []) or [],
        cuisine=payload.get("cuisine"),
        meal_type=payload.get("meal_type", []) or [],
        difficulty=payload.get("difficulty"),
        main_ingredient=payload.get("main_ingredient"),
        servings=payload.get("servings"),
        prep_time_minutes=payload.get("prep_time_minutes"),
        cook_time_minutes=payload.get("cook_time_minutes"),
        notes=payload.get("notes"),
    )


def _extract_anthropic(caption: str, transcript: str, image_path: Path | None) -> RecipeExtract:
    from anthropic import Anthropic

    model = os.environ.get("ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MODEL)
    client = Anthropic()

    content: list[dict[str, Any]] = []
    img = _image_bytes(image_path)
    if img:
        media_type, data = img
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": data},
        })
    content.append({"type": "text", "text": _user_text(caption, transcript)})

    response = client.messages.create(
        model=model,
        max_tokens=2048,
        system=[{
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }],
        tools=[{
            "name": TOOL_NAME,
            "description": TOOL_DESCRIPTION,
            "input_schema": RECIPE_SCHEMA,
        }],
        tool_choice={"type": "tool", "name": TOOL_NAME},
        messages=[{"role": "user", "content": content}],
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == TOOL_NAME:
            return _to_recipe(block.input)
    raise ExtractError(f"anthropic: no {TOOL_NAME} tool_use (stop_reason={response.stop_reason})")


def _extract_openrouter(caption: str, transcript: str, image_path: Path | None) -> RecipeExtract:
    from openai import OpenAI

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise ExtractError("OPENROUTER_API_KEY not set")
    model = os.environ.get("OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL)

    default_headers = {}
    if referer := os.environ.get("OPENROUTER_HTTP_REFERER"):
        default_headers["HTTP-Referer"] = referer
    if title := os.environ.get("OPENROUTER_APP_TITLE"):
        default_headers["X-Title"] = title

    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
        default_headers=default_headers or None,
    )

    user_content: list[dict[str, Any]] = []
    img = _image_bytes(image_path)
    if img:
        media_type, data = img
        user_content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{media_type};base64,{data}"},
        })
    user_content.append({"type": "text", "text": _user_text(caption, transcript)})

    response = client.chat.completions.create(
        model=model,
        max_tokens=2048,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        tools=[{
            "type": "function",
            "function": {
                "name": TOOL_NAME,
                "description": TOOL_DESCRIPTION,
                "parameters": RECIPE_SCHEMA,
            },
        }],
        tool_choice={"type": "function", "function": {"name": TOOL_NAME}},
    )

    msg = response.choices[0].message
    tool_calls = getattr(msg, "tool_calls", None) or []
    for call in tool_calls:
        if call.function.name == TOOL_NAME:
            try:
                payload = json.loads(call.function.arguments)
            except json.JSONDecodeError as e:
                raise ExtractError(f"openrouter: bad JSON in tool call: {e}") from e
            return _to_recipe(payload)
    raise ExtractError(f"openrouter: no {TOOL_NAME} tool call (finish={response.choices[0].finish_reason})")


def extract_recipe(
    caption: str,
    transcript: str,
    image_path: Path | None = None,
) -> RecipeExtract:
    provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()
    if provider == "anthropic":
        return _extract_anthropic(caption, transcript, image_path)
    if provider == "openrouter":
        return _extract_openrouter(caption, transcript, image_path)
    raise ExtractError(f"Unknown LLM_PROVIDER={provider!r} (expected anthropic|openrouter)")
