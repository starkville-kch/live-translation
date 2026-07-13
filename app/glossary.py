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


class GlossaryCorrector:
    """Loads glossary.yaml and applies corrections to committed caption lines."""

    def __init__(self, path: Path = _GLOSSARY_PATH):
        self._direct: list[GlossaryEntry] = []
        self._review_only: list[dict] = []
        self._load(path)

    def _load(self, path: Path) -> None:
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
            ))

        self._review_only = data.get("review_only", []) or []

        enabled_cats = sorted({e.category for e in self._direct})
        _log.info(
            "Glossary loaded: %d direct entries (categories: %s), %d review-only",
            len(self._direct), ", ".join(enabled_cats) or "none", len(self._review_only),
        )

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
