"""
app/languages.py — Model-Specific Language Catalog & Validation Module
=====================================================================
Loads static catalog for Gemini 3.5 Live Translate and provides lookup,
search, and validation utilities.
"""
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass(frozen=True)
class LanguageInfo:
    code: str
    name: str
    native_name: str
    aliases: List[str] = field(default_factory=list)

    def display_name(self) -> str:
        """Returns format like 'Українська (Ukrainian)' or 'English' if identical."""
        if self.native_name and self.native_name != self.name:
            return f"{self.native_name} ({self.name})"
        return self.name

    def matches(self, query: str) -> bool:
        q = query.strip().lower()
        if not q:
            return False
        if q == self.code.lower():
            return True
        if q in self.name.lower() or q in self.native_name.lower():
            return True
        return any(q in alias.lower() for alias in self.aliases)


@dataclass(frozen=True)
class LanguageCatalog:
    catalog_version: str
    model_family: str
    languages: List[LanguageInfo]
    _by_code: Dict[str, LanguageInfo] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: dict) -> "LanguageCatalog":
        langs = [
            LanguageInfo(
                code=item["code"].lower().strip(),
                name=item["name"].strip(),
                native_name=item.get("native_name", item["name"]).strip(),
                aliases=[a.lower().strip() for a in item.get("aliases", [])],
            )
            for item in data.get("languages", [])
        ]
        by_code = {lang.code: lang for lang in langs}
        return cls(
            catalog_version=data.get("catalog_version", "unknown"),
            model_family=data.get("model_family", "gemini-3.5-live-translate-preview"),
            languages=langs,
            _by_code=by_code,
        )

    def get(self, code: str) -> Optional[LanguageInfo]:
        return self._by_code.get(code.lower().strip()) if code else None

    def contains(self, code: str) -> bool:
        return (code.lower().strip() in self._by_code) if code else False

    def search(self, query: str) -> List[LanguageInfo]:
        return [lang for lang in self.languages if lang.matches(query)]

    def __len__(self) -> int:
        return len(self.languages)


_CATALOG_CACHE: Optional[LanguageCatalog] = None


def _resolve_catalog_path() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "app" / "data" / "languages.json"
    local_path = Path(__file__).parent / "data" / "languages.json"
    if local_path.exists():
        return local_path
    return Path(__file__).parent.parent / "app" / "data" / "languages.json"


def load_language_catalog() -> LanguageCatalog:
    global _CATALOG_CACHE
    if _CATALOG_CACHE is not None:
        return _CATALOG_CACHE

    path = _resolve_catalog_path()
    if not path.exists():
        raise FileNotFoundError(f"Language catalog not found at {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    _CATALOG_CACHE = LanguageCatalog.from_dict(data)
    return _CATALOG_CACHE


def get_language(code: str) -> Optional[LanguageInfo]:
    return load_language_catalog().get(code)


def is_valid_language_code(code: str) -> bool:
    return load_language_catalog().contains(code)


def parse_source_language_codes(source: str | list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Parse source language specifier into a tuple of clean language codes.
    
    Supports:
        - "any" -> ("any",)
        - "ko+en" or "ko,en" -> ("ko", "en")
        - ["ko", "en"] -> ("ko", "en")
    """
    if isinstance(source, (list, tuple)):
        items = []
        for s in source:
            items.extend(parse_source_language_codes(s))
        return tuple(dict.fromkeys(items))
    s = (source or "").strip().lower()
    if not s:
        return ()
    if s == "any":
        return ("any",)
    # Split composite strings like "ko+en" or "ko, en"
    delims = ["+", ","]
    parts = [s]
    for d in delims:
        next_parts = []
        for p in parts:
            next_parts.extend(p.split(d))
        parts = next_parts
    cleaned = [p.strip() for p in parts if p.strip()]
    return tuple(dict.fromkeys(cleaned))


def is_valid_source_language_code(code: str | list[str] | tuple[str, ...]) -> bool:
    parsed = parse_source_language_codes(code)
    if not parsed:
        return False
    if "any" in parsed:
        return len(parsed) == 1
    return all(is_valid_language_code(p) for p in parsed)


def get_available_languages() -> List[LanguageInfo]:
    return load_language_catalog().languages


def search_languages(query: str) -> List[LanguageInfo]:
    return load_language_catalog().search(query)
