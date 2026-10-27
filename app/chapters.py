"""
app/chapters.py - Automated YouTube Chapter Marker Extraction from Audio Classifier Transitions
================================================================================================
Derives church service liturgy chapters (Opening, Announcements, Praise & Worship, Sermon,
Response Hymn) from real-time audio classifier transitions.
"""
from typing import Any, Dict, List, Optional


def format_time_str(seconds: float) -> str:
    """Format seconds into M:SS or H:MM:SS format suitable for YouTube."""
    total_sec = max(0, int(round(seconds)))
    mins = total_sec // 60
    secs = total_sec % 60
    hours = mins // 60
    mins = mins % 60
    if hours > 0:
        return f"{hours}:{mins:02d}:{secs:02d}"
    return f"{mins}:{secs:02d}"


def compute_youtube_chapters(transitions: List[Dict[str, Any]], runtime: float = 0.0) -> Dict[str, Any]:
    """Extract liturgy chapters from audio classifier transitions.

    Returns a dictionary containing:
      - 'milestones': raw seconds and time strings for key service events
      - 'youtube_player_chapters': official YouTube scrub-bar chapter block starting at 0:00
      - 'inline_description': single-line inline format ('광고: 0:26  찬양: 4:14  설교시작: 27:14')
    """
    if not transitions:
        return {
            "milestones": {},
            "youtube_player_chapters": "0:00 예배 시작",
            "inline_description": "",
        }

    # Group transitions into continuous intervals
    segments = []
    for i in range(len(transitions)):
        t_start = float(transitions[i].get("t", 0.0))
        if i + 1 < len(transitions):
            t_end = float(transitions[i + 1].get("t", runtime))
        else:
            t_end = max(t_start, runtime)
        label = transitions[i].get("label", "uncertain")
        segments.append({
            "start": t_start,
            "end": t_end,
            "dur": max(0.0, t_end - t_start),
            "label": label,
        })

    # Announcements (광고): first speech segment within first 10 minutes
    ad_seg = next((s for s in segments if s["label"] == "speech" and s["start"] < 600), None)

    # Praise (찬양): first substantial music segment (> 60s) within first 30 minutes
    praise_seg = next((s for s in segments if s["label"] == "music" and s["dur"] >= 60 and s["start"] < 1800), None)

    # Sermon (설교시작): longest speech segment in service (must be >= 5 minutes)
    speech_segs = [s for s in segments if s["label"] == "speech" and s["dur"] >= 300]
    sermon_seg = max(speech_segs, key=lambda s: s["dur"]) if speech_segs else None

    # Response hymn (결단찬양): first music segment after sermon begins (must be >= 60s)
    resp_seg = None
    if sermon_seg:
        resp_seg = next(
            (s for s in segments if s["label"] == "music" and s["start"] > sermon_seg["start"] and s["dur"] >= 60),
            None,
        )

    milestones: Dict[str, Dict[str, Any]] = {}
    if ad_seg:
        milestones["announcements"] = {
            "seconds": round(ad_seg["start"], 1),
            "time_str": format_time_str(ad_seg["start"]),
        }
    if praise_seg:
        milestones["praise"] = {
            "seconds": round(praise_seg["start"], 1),
            "time_str": format_time_str(praise_seg["start"]),
        }
    if sermon_seg:
        milestones["sermon"] = {
            "seconds": round(sermon_seg["start"], 1),
            "time_str": format_time_str(sermon_seg["start"]),
        }
    if resp_seg:
        milestones["response_hymn"] = {
            "seconds": round(resp_seg["start"], 1),
            "time_str": format_time_str(resp_seg["start"]),
        }

    # Format 1: Official YouTube Player Chapters (must start with 0:00)
    player_chapters = ["0:00 예배 시작"]
    if ad_seg and round(ad_seg["start"]) > 5:
        player_chapters.append(f"{milestones['announcements']['time_str']} 광고")
    if praise_seg:
        player_chapters.append(f"{milestones['praise']['time_str']} 찬양")
    if sermon_seg:
        player_chapters.append(f"{milestones['sermon']['time_str']} 설교 말씀")
    if resp_seg:
        player_chapters.append(f"{milestones['response_hymn']['time_str']} 결단 찬양")

    # Format 2: Inline description format for bulletin/website
    desc_lines = []
    if ad_seg:
        desc_lines.append(f"광고: {milestones['announcements']['time_str']}")
    if praise_seg:
        desc_lines.append(f"찬양: {milestones['praise']['time_str']}")
    if sermon_seg:
        desc_lines.append(f"설교시작: {milestones['sermon']['time_str']}")
    if resp_seg:
        desc_lines.append(f"결단찬양: {milestones['response_hymn']['time_str']}")

    return {
        "milestones": milestones,
        "youtube_player_chapters": "\n".join(player_chapters),
        "inline_description": "  ".join(desc_lines),
    }
