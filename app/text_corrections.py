"""Local corrections for committed text; never supplied to the model."""
import re
from pathlib import Path

import yaml

from app.config import get_app_root


def collapse_repetitions(text: str) -> str:
    """Collapse five or more consecutive copies of a word or short phrase."""
    words = re.compile(r"(?<!\w)(?P<phrase>\w{1,40}(?:[ \t]+\w{1,40}){0,5}?)(?:[ \t,;.!?]+(?P=phrase)){4,}(?!\w)")
    text = words.sub(lambda match: match.group("phrase") if any(char.isalpha() for char in match.group("phrase")) else match.group(), text)
    return re.sub(r"(?P<phrase>[가-힣]{1,40}?)(?P=phrase){4,}", lambda match: match.group("phrase"), text)


class NameCorrector:
    def __init__(self, path: Path | None = None):
        source = path or get_app_root() / "config" / "name_corrections.yaml"
        data = yaml.safe_load(source.read_text(encoding="utf-8")) if source.exists() else {}
        self._pairs = data or {}
        patterns = []
        for phrase in sorted(self._pairs, key=len, reverse=True):
            escaped = re.escape(phrase)
            if phrase.isascii():
                escaped = rf"(?<!\w){escaped}(?!\w)"
            patterns.append(escaped)
        self._pattern = re.compile("|".join(patterns)) if patterns else None

    def correct(self, text: str) -> str:
        if self._pattern:
            text = self._pattern.sub(lambda match: self._pairs[match.group()], text)
        return collapse_repetitions(text)
