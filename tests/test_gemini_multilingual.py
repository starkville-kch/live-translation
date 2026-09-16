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
    assert session.expected_source_language == "ko+en"
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


def test_evaluate_drift_score_any_source():
    # When expected_source is 'any', any input language is allowed without penalty
    for in_lang, in_txt in [("ko", "은혜"), ("en", "grace"), ("es", "gracia"), ("ja", "恵み")]:
        score = evaluate_drift_score(
            input_lang=in_lang,
            input_text=in_txt,
            output_lang="uk",
            output_text="Благодать",
            expected_source="any",
            target_language="uk",
        )
        assert score == 0, f"Expected 0 for input {in_lang}, got {score}"

    # If output does not match target, still flagged as +2
    score_wrong = evaluate_drift_score(
        input_lang="ko",
        input_text="은혜",
        output_lang="es",
        output_text="gracia",
        expected_source="any",
        target_language="uk",
    )
    assert score_wrong == 2


def test_evaluate_drift_score_en_to_zh_chinese_output():
    # Chinese characters output must not be falsely flagged as drift
    score = evaluate_drift_score(
        input_lang="en",
        input_text="Grace and peace",
        output_lang="zh",
        output_text="恩典与平安",
        expected_source="en",
        target_language="zh",
    )
    assert score == 0


def test_gemini_session_drift_window_and_threshold():
    session = GeminiSession(
        on_caption=MagicMock(),
        target_language_code="zh",
        expected_source_language="any",
        drift_window=2,
        drift_threshold=3,
    )
    assert session._drift_window == 2
    assert session._drift_threshold == 3
    assert session._drift_history.maxlen == 2


def test_evaluate_drift_score_ko_plus_en_bilingual():
    """Verify 'ko+en' accepts both Korean and English turns with 0 drift, but flags 3rd languages."""
    # Korean input -> valid
    score_ko = evaluate_drift_score(
        input_lang="ko",
        input_text="하나님의 은혜",
        output_lang="en",
        output_text="God's grace",
        expected_source="ko+en",
        target_language="en",
    )
    assert score_ko == 0

    # English input (e.g. prayer / announcements) -> valid
    score_en = evaluate_drift_score(
        input_lang="en",
        input_text="Let us pray together",
        output_lang="en",
        output_text="Let us pray together",
        expected_source="ko+en",
        target_language="en",
    )
    assert score_en == 0

    # Japanese input -> flagged as drift (+1)
    score_ja = evaluate_drift_score(
        input_lang="ja",
        input_text="おはようございます",
        output_lang="en",
        output_text="Good morning",
        expected_source="ko+en",
        target_language="en",
    )
    assert score_ja == 1

    # Fallback script heuristic: Japanese text when input_lang is None -> flagged (+1)
    score_script = evaluate_drift_score(
        input_lang=None,
        input_text="カタカナ",
        output_lang="en",
        output_text="Katakana",
        expected_source="ko+en",
        target_language="en",
    )
    assert score_script == 1


def test_evaluate_drift_score_es_plus_en_multilingual():
    """Verify 'es+en' accepts Spanish and English input with 0 drift, but flags Japanese (+1)."""
    # Spanish input -> 0
    assert evaluate_drift_score(
        input_lang="es",
        input_text="Dios les bendiga",
        output_lang="en",
        output_text="God bless you",
        expected_source="es+en",
        target_language="en",
    ) == 0

    # English input -> 0
    assert evaluate_drift_score(
        input_lang="en",
        input_text="Amen",
        output_lang="en",
        output_text="Amen",
        expected_source="es+en",
        target_language="en",
    ) == 0

    # Japanese input -> +1
    assert evaluate_drift_score(
        input_lang="ja",
        input_text="ありがとう",
        output_lang="en",
        output_text="Thank you",
        expected_source="es+en",
        target_language="en",
    ) == 1


def test_evaluate_drift_score_any_autodetect():
    """Verify 'any' continuous auto-detect mode accepts any input language without input drift penalty."""
    for lang, sample in [("ko", "안녕하세요"), ("es", "Hola"), ("uk", "Привіт"), ("ja", "こんにちは"), ("zh", "你好")]:
        assert evaluate_drift_score(
            input_lang=lang,
            input_text=sample,
            output_lang="en",
            output_text="Hello",
            expected_source="any",
            target_language="en",
        ) == 0


