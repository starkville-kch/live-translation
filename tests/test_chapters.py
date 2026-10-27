"""
tests/test_chapters.py - Unit Tests for YouTube Chapter Extraction
"""
import pytest
from app.chapters import format_time_str, compute_youtube_chapters


def test_format_time_str():
    assert format_time_str(0) == "0:00"
    assert format_time_str(26.4) == "0:26"
    assert format_time_str(254) == "4:14"
    assert format_time_str(1634) == "27:14"
    assert format_time_str(3665) == "1:01:05"


def test_compute_youtube_chapters_empty():
    res = compute_youtube_chapters([])
    assert res["milestones"] == {}
    assert "0:00 예배 시작" in res["youtube_player_chapters"]


def test_compute_youtube_chapters_worship_flow():
    transitions = [
        {"t": 20.0, "label": "speech"},    # Announcements (0:20)
        {"t": 254.0, "label": "music"},    # Praise (4:14)
        {"t": 800.0, "label": "speech"},   # Responsive reading
        {"t": 1634.0, "label": "speech"},  # Sermon start (27:14)
        {"t": 3200.0, "label": "music"},   # Response hymn (53:20)
        {"t": 3500.0, "label": "speech"},  # Benediction
    ]
    res = compute_youtube_chapters(transitions, runtime=3600.0)

    # Milestones
    assert res["milestones"]["announcements"]["time_str"] == "0:20"
    assert res["milestones"]["praise"]["time_str"] == "4:14"
    assert res["milestones"]["sermon"]["time_str"] == "27:14"
    assert res["milestones"]["response_hymn"]["time_str"] == "53:20"

    # Official YouTube Player Chapters
    player_chapters = res["youtube_player_chapters"].splitlines()
    assert player_chapters[0] == "0:00 예배 시작"
    assert "0:20 광고" in player_chapters
    assert "4:14 찬양" in player_chapters
    assert "27:14 설교 말씀" in player_chapters
    assert "53:20 결단 찬양" in player_chapters

    # Inline Description Format
    assert "광고: 0:20" in res["inline_description"]
    assert "찬양: 4:14" in res["inline_description"]
    assert "설교시작: 27:14" in res["inline_description"]
