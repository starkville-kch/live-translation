"""
tests/test_languages.py — Unit Tests for Model-Specific Language Catalog
"""
import pytest
from app.languages import (
    load_language_catalog,
    get_language,
    is_valid_language_code,
    get_available_languages,
    search_languages,
    LanguageInfo,
)


def test_language_catalog_loading():
    cat = load_language_catalog()
    assert cat is not None
    assert cat.catalog_version == "2026-09"
    assert "live-translate" in cat.model_family
    # Gemini 3.5 Live Translate supports 70+ languages
    assert len(cat) >= 70


def test_language_catalog_uniqueness():
    cat = load_language_catalog()
    codes = [lang.code for lang in cat.languages]
    assert len(codes) == len(set(codes)), "Language codes must be strictly unique"


def test_core_validation_languages_exist():
    # Required Phase 24 targets and baseline languages
    for code in ["en", "uk", "zh", "ko", "es", "de", "fr", "ja", "vi"]:
        assert is_valid_language_code(code), f"Expected language {code} to be in catalog"
        info = get_language(code)
        assert info is not None
        assert info.code == code
        assert len(info.name) > 0
        assert len(info.native_name) > 0


def test_language_display_name_and_search():
    uk = get_language("uk")
    assert uk is not None
    assert uk.name == "Ukrainian"
    assert uk.native_name == "Українська"
    assert uk.display_name() == "Українська (Ukrainian)"
    assert uk.matches("uk")
    assert uk.matches("ukrainian")
    assert uk.matches("Українська")

    en = get_language("en")
    assert en is not None
    assert en.display_name() == "English"

    results = search_languages("mandarin")
    assert any(lang.code == "zh" for lang in results)


def test_invalid_language_lookup():
    assert not is_valid_language_code("invalid_code_xyz")
    assert not is_valid_language_code("")
    assert get_language("invalid_code_xyz") is None
