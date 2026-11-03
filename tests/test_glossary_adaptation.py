"""
tests/test_glossary_adaptation.py — Tests for Glossary Adaptation & Post-Sermon Review
"""
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from fastapi.testclient import TestClient

from app.gemini_session import GeminiSession, TranscriptEntry
from app.glossary import GlossaryCorrector, GlossaryEntry
from app.server import app, _glossary, _last_session_transcript


def test_custom_vocabulary_contains_required_terms():
    corrector = GlossaryCorrector()
    vocab = corrector.get_custom_vocabulary()

    # Core church polity & office terms
    assert "당회" in vocab
    assert "노회" in vocab
    assert "총회" in vocab
    assert "장로" in vocab
    assert "집사" in vocab
    assert "목사" in vocab

    # Korean church titles requested
    assert "권사" in vocab
    assert "권사님" in vocab
    assert "사모" in vocab
    assert "사모님" in vocab
    assert "구역예배" in vocab
    assert "스탁빌 한인교회" in vocab

    # Reformed doctrinal terms requested
    assert "예정" in vocab
    assert "칭의" in vocab
    assert "성화" in vocab

    # Pastor loanword / Konglish idioms requested
    assert "난 멤버" in vocab
    assert "난멤버" in vocab


def test_gemini_session_config_wires_custom_vocabulary():
    mock_glossary = MagicMock()
    mock_glossary.get_custom_vocabulary.return_value = ["권사님", "사모님", "예정", "난 멤버"]

    session = GeminiSession(
        on_caption=lambda c: None,
        glossary=mock_glossary,
        target_language_code="en",
        expected_source_language="ko+en",
    )

    config = session._build_config("gemini-3.5-live-translate-preview")
    assert config.input_audio_transcription is not None
    assert config.input_audio_transcription.language_codes == ["ko", "en"]
    assert config.input_audio_transcription.custom_vocabulary == ["권사님", "사모님", "예정", "난 멤버"]


def test_candidate_harvester_extracts_sermon_terms(tmp_path: Path):
    dummy_yaml = tmp_path / "glossary.yaml"
    dummy_yaml.write_text("direct: []\nreview_only: []\n", encoding="utf-8")
    corrector = GlossaryCorrector(dummy_yaml)

    sample_turns = [
        TranscriptEntry(timestamp=1.0, source="오늘 예배에 참석하신 권사님들과 성도님들을 환영합니다.", target="Welcome deaconesses and members."),
        TranscriptEntry(timestamp=2.0, source="바울 사도는 하나님의 예정과 칭의에 대하여 가르쳤습니다.", target="Paul taught about predestination and justification."),
        TranscriptEntry(timestamp=3.0, source="우리 교회에 새로 오신 분 중에 난 멤버 분들도 계십니다.", target="Some newcomers are non-members."),
        TranscriptEntry(timestamp=4.0, source="이것은 난멤버 사역의 중요한 부분입니다.", target="This is important for non-member ministry."),
    ]

    candidates = corrector.extract_candidates(sample_turns)
    assert len(candidates) > 0

    candidate_kos = [c["ko"] for c in candidates]
    assert "권사님" in candidate_kos
    assert "예정" in candidate_kos
    assert "칭의" in candidate_kos
    assert "난 멤버" in candidate_kos or "난멤버" in candidate_kos

    # Verify counts and examples
    non_member_candidate = next(c for c in candidates if c["ko"] in ("난 멤버", "난멤버"))
    assert non_member_candidate["count"] >= 1
    assert "새로 오신" in non_member_candidate["example"] or "사역" in non_member_candidate["example"]


def test_glossary_add_entry_persists(tmp_path: Path):
    yaml_path = tmp_path / "glossary.yaml"
    yaml_path.write_text("direct:\n  - category: A\n    enabled: true\n    ko: 당회\n    en: Session\n\nreview_only:\n  - ko: 주일\n", encoding="utf-8")
    corrector = GlossaryCorrector(yaml_path)

    assert corrector.entry_count == 1

    success = corrector.add_entry(
        ko="특순",
        en="special praise",
        category="B",
        variants=["특별순서"],
    )
    assert success is True
    assert corrector.entry_count == 2
    assert "특순" in corrector.get_custom_vocabulary()
    assert "특별순서" in corrector.get_custom_vocabulary()

    # Re-read YAML to ensure on-disk persistence
    reloaded = GlossaryCorrector(yaml_path)
    assert reloaded.entry_count == 2
    assert "특순" in reloaded.get_custom_vocabulary()


def test_api_glossary_endpoints(monkeypatch):
    client = TestClient(app)

    # 1. Get entries
    resp = client.get("/api/glossary/entries")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "entries" in data
    assert data["count"] > 0

    # 2. Get candidates
    resp = client.get("/api/glossary/candidates")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "candidates" in data

    # 3. Add entry (mock persistence to avoid mutating repo config/glossary.yaml)
    monkeypatch.setattr(_glossary, "add_entry", lambda **kwargs: True)
    add_payload = {
        "ko": "새가족",
        "en": "newcomer / new family",
        "category": "F",
        "variants": ["새신자"],
    }
    resp = client.post("/api/glossary/add", json=add_payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "새가족" in data["message"]
