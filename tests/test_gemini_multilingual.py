"""
tests/test_gemini_multilingual.py — Unit Tests for Language-Neutral GeminiSession & CaptionEvent
"""
import pytest
from unittest.mock import MagicMock
from google.genai import types

from app.broadcast import CaptionEvent, CaptionBroadcaster
from app.gemini_session import (
    GeminiSession,
    TranscriptEntry,
    evaluate_drift_score,
)


def test_default_session_parameters():
    session = GeminiSession(on_caption=MagicMock())
    assert session.target_language_code == "en"
    assert session.expected_source_language == "ko"
    assert session.tag == "Gemini:en"


def test_explicit_multilingual_session_parameters():
    session = GeminiSession(
        on_caption=MagicMock(),
        target_language_code="uk",
        expected_source_language="en",
    )
    assert session.target_language_code == "uk"
    assert session.expected_source_language == "en"
    assert session.tag == "Gemini:uk"


def test_translation_config_receives_target_language_code():
    session_uk = GeminiSession(
        on_caption=MagicMock(),
        target_language_code="uk",
        expected_source_language="ko",
    )
    config = session_uk._build_config("gemini-3.5-live-translate-preview")
    assert isinstance(config.translation_config, types.TranslationConfig)
    assert config.translation_config.target_language_code == "uk"
    assert config.translation_config.echo_target_language is True


def test_caption_event_neutral_and_legacy_compatibility():
    # Construct with neutral fields
    ev1 = CaptionEvent(
        kind="commit",
        source="말씀",
        target="The Word",
        source_lang="ko",
        target_lang="en",
    )
    assert ev1.kind == "commit"
    assert ev1.source == "말씀"
    assert ev1.target == "The Word"
    assert ev1.text == "The Word"  # backward compatibility alias
    assert ev1.ko == "말씀"        # backward compatibility alias
    assert ev1.source_lang == "ko"
    assert ev1.target_lang == "en"

    # Construct with legacy kwargs (text, ko)
    ev2 = CaptionEvent(kind="commit", text="The Grace", ko="은혜")
    assert ev2.target == "The Grace"
    assert ev2.source == "은혜"
    assert ev2.text == "The Grace"
    assert ev2.ko == "은혜"


def test_transcript_entry_neutral_and_legacy_compatibility():
    entry = TranscriptEntry(
        timestamp=100.0,
        source="말씀을 듣습니다",
        target="We hear the Word",
        source_lang="ko",
        target_lang="en",
    )
    assert entry.source == "말씀을 듣습니다"
    assert entry.target == "We hear the Word"
    assert entry.korean == "말씀을 듣습니다"
    assert entry.english == "We hear the Word"
    assert entry.source_lang == "ko"
    assert entry.target_lang == "en"


def test_evaluate_drift_score_multilingual():
    # Normal Ukrainian output for Ukrainian session -> score 0
    score_uk = evaluate_drift_score(
        input_lang="en",
        input_text="The grace of God",
        output_lang="uk",
        output_text="Благодать Божа",
        expected_source="en",
        target_language="uk",
    )
    assert score_uk == 0

    # Output not matching target language -> score +2
    score_wrong_tgt = evaluate_drift_score(
        input_lang="en",
        input_text="The grace of God",
        output_lang="es",  # output is Spanish instead of Ukrainian
        output_text="La gracia de Dios",
        expected_source="en",
        target_language="uk",
    )
    assert score_wrong_tgt == 2


def test_instantiate_non_korean_non_english_session_without_hardcoding():
    # Instantiate French -> Chinese session
    session = GeminiSession(
        on_caption=MagicMock(),
        target_language_code="zh",
        expected_source_language="fr",
    )
    assert session.target_language_code == "zh"
    assert session.expected_source_language == "fr"
    assert session.tag == "Gemini:zh"
    assert session._current_source == ""
    assert session._current_target == ""
    assert session._current_ko == ""  # alias
    assert session._current_en == ""  # alias
