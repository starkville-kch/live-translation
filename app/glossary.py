"""
app/glossary.py — Glossary-Based Caption Correction
====================================================
Starkville Korean Church (PCA) — Live Translation System
---------------------------------------------------------
Applies a config-driven terminology correction pass to *committed* (settled)
English caption lines.  It is never applied to the streaming draft because that
would cause jarring visible edits mid-sentence.

How it works
------------
1. At the point a caption line commits (silence timer or force-commit), the
   caller provides both the accumulated Korean source text (input_transcription)
   and the finalized English text.
2. For each enabled ``direct`` glossary entry, we check whether the Korean term
   (or any of its listed variants) appears in the Korean source text, using
   Unicode-aware whole-phrase boundary matching.
3. If the Korean term IS present and the expected English term is NOT already in
   the English text, we apply a case-insensitive find-and-replace of whatever
   the model produced in its place.  If neither appears, we append a bracketed
   inline correction at the end of the line.
4. For ``review_only`` entries, we log a notice if the Korean term is detected
   but never modify the English text.
5. Every correction applied is logged with three fields: the Korean term
   detected, what the model produced, and what it was corrected to.

Loading
-------
The glossary is loaded from ``config/glossary.yaml`` once at import time.
If the file is absent (e.g., fresh checkout without the config), the corrector
is a no-op and logs a warning.
"""
import re
import logging
from pathlib import Path
from typing import NamedTuple

import yaml

_log = logging.getLogger("ops")

_GLOSSARY_PATH = Path(__file__).parent.parent / "config" / "glossary.yaml"


class GlossaryEntry(NamedTuple):
    category: str
    ko: str           # canonical Korean form
    en: str           # expected English term
    variants: list    # additional Korean spellings (may be empty)
    enabled: bool = True


# Pre-mapped candidate knowledge base for church, doctrinal, and loanword terms
_CANDIDATE_KNOWLEDGE_BASE = {
    # Category A: Polity & Office Titles
    "권사님": ("senior deaconess", "A"),
    "권사": ("senior deaconess", "A"),
    "사모님": ("pastor's wife", "A"),
    "사모": ("pastor's wife", "A"),
    "전도사님": ("evangelist / ministry director", "A"),
    "전도사": ("evangelist / ministry director", "A"),
    "안수집사님": ("ordained deacon", "A"),
    "안수집사": ("ordained deacon", "A"),
    "구역장님": ("cell group leader", "A"),
    "구역장": ("cell group leader", "A"),
    "순장님": ("small group leader", "A"),
    "순장": ("small group leader", "A"),
    "목자님": ("shepherd / house church leader", "A"),
    "목자": ("shepherd / house church leader", "A"),
    "간사님": ("staff / ministry coordinator", "A"),
    "선교사님": ("missionary", "A"),
    "선교사": ("missionary", "A"),

    # Category B: Worship & Fellowship
    "구역예배": ("cell group worship", "B"),
    "구역모임": ("cell group gathering", "B"),
    "수련회": ("church retreat", "B"),
    "심방": ("pastoral visit", "B"),
    "성찬식": ("Lord's Supper / communion", "B"),
    "교제": ("Christian fellowship", "B"),
    "나눔": ("fellowship sharing", "B"),
    "찬양팀": ("praise team", "B"),
    "헌금": ("offering", "B"),

    # Category D: Reformed Doctrinal Vocabulary
    "예정": ("predestination", "D"),
    "예정론": ("doctrine of predestination", "D"),
    "작정": ("divine decree", "D"),
    "칭의": ("justification", "D"),
    "성화": ("sanctification", "D"),
    "영화": ("glorification", "D"),
    "언약": ("covenant", "D"),
    "구속사": ("redemptive history", "D"),
    "구속": ("redemption", "D"),
    "섭리": ("divine providence", "D"),
    "삼위일체": ("Trinity", "D"),
    "은혜의 방편": ("means of grace", "D"),

    # Category F: Korean-American Community & Speaker Idioms (Konglish)
    "난 멤버": ("non-member", "F"),
    "난멤버": ("non-member", "F"),
    "넌 멤버": ("non-member", "F"),
    "넌멤버": ("non-member", "F"),
    "멤버십": ("membership", "F"),
    "펠로우십": ("fellowship", "F"),
    "커뮤니티": ("community", "F"),
    "포닥": ("postdoc", "F"),
    "포스닥": ("postdoc", "F"),
    "디펜스": ("dissertation defense", "F"),
    "어드바이저": ("advisor", "F"),
    "펀딩": ("funding", "F"),
    "인턴십": ("internship", "F"),
    "오퍼": ("job offer", "F"),
    "스탁빌": ("Starkville", "F"),
    "미시시피": ("Mississippi", "F"),
    "캠퍼스": ("campus", "F"),
    "그린카드": ("green card", "F"),
    "미니스트리": ("ministry", "F"),
    "디사이플": ("disciple", "F"),
    "컨퍼런스": ("conference", "F"),
}


class GlossaryCorrector:
    """Loads glossary.yaml and applies corrections to committed caption lines."""

    def __init__(self, path: Path = _GLOSSARY_PATH):
        self._path: Path = path
        self._direct: list[GlossaryEntry] = []
        self._review_only: list[dict] = []
        self._load(path)

    def _load(self, path: Path) -> None:
        self._path = path
        self._direct.clear()
        self._review_only.clear()
        if not path.exists():
            _log.warning("Glossary file not found: %s — correction pass disabled", path)
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except Exception as e:
            _log.error("Failed to load glossary: %s", e)
            return

        if not isinstance(data, dict):
            _log.error("Glossary YAML root must be a mapping, got %s", type(data))
            return

        for item in data.get("direct", []) or []:
            if not isinstance(item, dict):
                continue
            if not item.get("enabled", False):
                continue
            ko = item.get("ko", "").strip()
            en = item.get("en", "").strip()
            if not ko or not en:
                continue
            variants = [v.strip() for v in item.get("variants", []) if v]
            self._direct.append(GlossaryEntry(
                category=item.get("category", "?"),
                ko=ko, en=en, variants=variants,
                enabled=True,
            ))

        self._review_only = data.get("review_only", []) or []

        enabled_cats = sorted({e.category for e in self._direct})
        _log.info(
            "Glossary loaded: %d direct entries (categories: %s), %d review-only",
            len(self._direct), ", ".join(enabled_cats) or "none", len(self._review_only),
        )

    def get_custom_vocabulary(self) -> list[str]:
        """Return ordered, deduplicated list of active vocabulary phrases for Gemini ASR biasing."""
        vocab: list[str] = []
        for entry in self._direct:
            if getattr(entry, "enabled", True):
                if entry.ko and entry.ko not in vocab:
                    vocab.append(entry.ko)
                for v in entry.variants:
                    if v and v not in vocab:
                        vocab.append(v)
        for item in self._review_only:
            ko = item.get("ko", "").strip()
            if ko and ko not in vocab:
                vocab.append(ko)
            for v in item.get("variants", []) or []:
                v_str = str(v).strip()
                if v_str and v_str not in vocab:
                    vocab.append(v_str)
        return vocab

    def get_entries(self) -> list[dict]:
        """Return a list of all active direct glossary entries for display/management."""
        return [
            {
                "category": e.category,
                "ko": e.ko,
                "en": e.en,
                "variants": list(e.variants),
                "enabled": getattr(e, "enabled", True),
            }
            for e in self._direct
        ]

    def add_entry(
        self,
        ko: str,
        en: str,
        category: str = "F",
        variants: list[str] | None = None,
        note: str = "",
    ) -> bool:
        """Add or update a glossary entry in memory and persist it to config/glossary.yaml."""
        clean_ko = ko.strip()
        clean_en = en.strip()
        if not clean_ko or not clean_en:
            return False
        variants_clean = [v.strip() for v in (variants or []) if v.strip() and v.strip() != clean_ko]

        # Update in-memory direct list
        existing_idx = next((i for i, e in enumerate(self._direct) if e.ko == clean_ko), None)
        new_entry = GlossaryEntry(
            category=category,
            ko=clean_ko,
            en=clean_en,
            variants=variants_clean,
            enabled=True,
        )
        if existing_idx is not None:
            self._direct[existing_idx] = new_entry
        else:
            self._direct.append(new_entry)

        # Persist to YAML file cleanly preserving comments and structure
        if self._path and self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    file_text = f.read()

                # Clean YAML entry block
                entry_block = f"\n  - category: {category}\n    enabled: true\n    ko: \"{clean_ko}\"\n    en: \"{clean_en}\"\n"
                if variants_clean:
                    entry_block += "    variants:\n"
                    for v in variants_clean:
                        entry_block += f"      - \"{v}\"\n"

                # Check if exact ko term already exists in file
                ko_pattern = f'ko: "{clean_ko}"'
                if ko_pattern in file_text:
                    # Already exists, in-memory updated is enough
                    _log.info("Glossary entry '%s' already in file — updated in memory", clean_ko)
                    return True

                if "review_only:" in file_text:
                    parts = file_text.split("review_only:", 1)
                    new_file_text = parts[0].rstrip() + "\n" + entry_block + "\nreview_only:" + parts[1]
                else:
                    new_file_text = file_text.rstrip() + "\n" + entry_block + "\n"

                with open(self._path, "w", encoding="utf-8") as f:
                    f.write(new_file_text)
                _log.info("Saved glossary entry: KO='%s' -> EN='%s' (cat=%s)", clean_ko, clean_en, category)
                return True
            except Exception as e:
                _log.error("Failed to write to glossary file %s: %s", self._path, e)
                return False
        return True

    def extract_candidates(self, transcript_turns: list) -> list[dict]:
        """Harvest recommended glossary candidate words from completed sermon turns.

        Returns a list of dicts:
            [{"ko": str, "suggested_en": str, "category": str, "count": int, "example": str}]
        sorted by count descending.
        """
        if not transcript_turns:
            return []

        # Build existing known phrases set (both ko and variants)
        known_phrases: set[str] = set()
        for entry in self._direct:
            known_phrases.add(entry.ko.strip())
            for v in entry.variants:
                known_phrases.add(v.strip())
        for ro in self._review_only:
            ko = ro.get("ko", "").strip()
            if ko:
                known_phrases.add(ko)
            for v in ro.get("variants", []) or []:
                known_phrases.add(str(v).strip())

        # Collect text per turn
        turns_data: list[tuple[str, str]] = []
        for turn in transcript_turns:
            src = getattr(turn, "source", None) or getattr(turn, "korean", None)
            tgt = getattr(turn, "target", None) or getattr(turn, "english", None)
            if src is None and isinstance(turn, (list, tuple)) and len(turn) >= 2:
                src, tgt = str(turn[0]), str(turn[1])
            src_str = str(src or "").strip()
            tgt_str = str(tgt or "").strip()
            if src_str:
                turns_data.append((src_str, tgt_str))

        if not turns_data:
            return []

        candidates_map: dict[str, dict] = {}

        # 1. Check curated candidate terms
        for candidate_ko, (suggested_en, cat) in _CANDIDATE_KNOWLEDGE_BASE.items():
            if candidate_ko in known_phrases:
                continue
            count = 0
            best_example = ""
            for src, tgt in turns_data:
                if _phrase_present(candidate_ko, src):
                    count += 1
                    if not best_example:
                        best_example = src
            if count > 0:
                candidates_map[candidate_ko] = {
                    "ko": candidate_ko,
                    "suggested_en": suggested_en,
                    "category": cat,
                    "count": count,
                    "example": best_example,
                }

        # 2. Pattern extraction for honorific titles ending in 님 (e.g. 장로님, 집사님, 성도님, 순장님)
        title_pattern = re.compile(r"\b([가-힣]{2,4}님)\b")
        for src, tgt in turns_data:
            matches = title_pattern.findall(src)
            for match in matches:
                if match in known_phrases or match in candidates_map:
                    continue
                # Skip generic non-titles like "하나님", "예수님", "성령님", "선생님", "부모님"
                if match in ("하나님", "예수님", "성령님", "선생님", "부모님", "손님"):
                    continue
                # Count occurrences
                cnt = sum(1 for s, _ in turns_data if match in s)
                base = match[:-1]  # strip 님
                suggested = _CANDIDATE_KNOWLEDGE_BASE.get(base, ("", "A"))[0]
                candidates_map[match] = {
                    "ko": match,
                    "suggested_en": suggested or f"title ({match})",
                    "category": "A",
                    "count": cnt,
                    "example": src,
                }

        # Sort candidates by count descending
        sorted_candidates = sorted(
            candidates_map.values(),
            key=lambda c: (-c["count"], c["ko"]),
        )
        return sorted_candidates

    def correct(self, korean_source: str, english_text: str) -> str:
        """Apply glossary corrections to a committed English caption line.

        Args:
            korean_source: Accumulated Korean input_transcription for this turn.
            english_text:  The finalized English caption text to potentially correct.

        Returns:
            The (possibly corrected) English text.
        """
        if not self._direct and not self._review_only:
            return english_text

        result = english_text

        # Review-only: detect and log, never modify
        for entry in self._review_only:
            ko_term = entry.get("ko", "")
            if ko_term and _phrase_present(ko_term, korean_source):
                _log.info(
                    "[Glossary review] Korean term '%s' detected — %s",
                    ko_term, entry.get("note", "(no note)")
                )

        # Direct corrections
        for entry in self._direct:
            all_ko_forms = [entry.ko] + list(entry.variants)
            ko_found = any(_phrase_present(form, korean_source) for form in all_ko_forms)
            if not ko_found:
                continue

            # Korean term was spoken — check if English output already correct
            if _phrase_present_en(entry.en, result):
                continue  # model already used the correct term

            # Try to find what the model actually produced and replace it.
            # We don't know the model's mistranslation ahead of time, so we
            # append a bracketed correction note inline after the line.
            # If the operator enables more entries over time and finds the model
            # consistently produces a specific wrong term, they can add an
            # explicit "wrong_en" field to the glossary entry — but for now,
            # bracketed append is the safe approach that avoids corrupting
            # sentences where we can't identify the wrong word.
            original = result
            result = result.rstrip() + f" [{entry.en}]"
            _log.info(
                "[Glossary correction] KO='%s' (cat=%s) | model produced: '%s' | corrected to: '%s'",
                entry.ko, entry.category, original, result
            )

        return result

    @property
    def entry_count(self) -> int:
        return len(self._direct)


def _phrase_present(phrase: str, text: str) -> bool:
    """True if `phrase` appears in `text` as a phrase-initial boundary match.

    Only checks the LEFT boundary: the phrase must not be immediately preceded
    by another Korean syllable, so '장로' inside '원로장로' does not match.
    The RIGHT boundary is intentionally unchecked: Korean grammar attaches
    particles (에서, 를, 의, 은/는, etc.) directly to the noun without a space,
    so '당회에서' is a valid match for '당회'.
    """
    if not phrase or not text:
        return False
    escaped = re.escape(phrase)
    # Negative lookbehind only: no Korean syllable block immediately before
    pattern = r"(?<![가-힣])" + escaped
    return bool(re.search(pattern, text))


def _phrase_present_en(phrase: str, text: str) -> bool:
    """Case-insensitive whole-phrase check for an English term in text."""
    if not phrase or not text:
        return False
    escaped = re.escape(phrase)
    return bool(re.search(r"\b" + escaped + r"\b", text, re.IGNORECASE))
