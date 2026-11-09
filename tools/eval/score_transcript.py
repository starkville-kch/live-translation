#!/usr/bin/env python3
"""Score a live-translation transcript against a word-by-word reference.

Developer tool only. Not part of the app or the EXE. Requires numpy.

Usage:
    python score_transcript.py REFERENCE.md LIVE_TRANSCRIPT.md [--facts FACTS.json] [--json OUT.json]

What it measures (Korean source text only, unless noted):
  1. Per-section character error rate (CER) for each "## " section of the
     reference. Each section is located in the live text by semi-global
     alignment (free start and end in the live text), so the reference's
     timestamps are never used. Spaces and punctuation are removed first,
     because Korean word spacing is inconsistent.
  2. Speech-only CER: sections whose title matches --music-pattern (songs,
     practice) are reported but excluded from this total.
  3. Key facts (optional --facts): regex checks on the Korean source and the
     English output, including known wrong variants (e.g. "Starfield").
  4. English-in-sermon check: for Latin-script phrases in the reference, the
     share of their content words that appear in Latin script in the live
     Korean source and in the English output. Position-agnostic, so it is a
     rough indicator, not an accuracy score.

Reference formats (detected automatically):
  - "## " sections with titles (e.g. the Gemini word-by-word transcript).
  - "# N분부터" five-minute bins with **[hh:mm:ss]** entries (e.g. the ChatGPT
    transcript). Entries labeled 찬양 and [..] markers are dropped, so each bin
    holds speech only.

Limits: CER counts English rendered in Hangul (e.g. 유니포미티) as errors.
There is no English reference, so English output quality is only checked
through the key facts.
"""
import argparse
import json
import re
import sys

import numpy as np

MUSIC_PATTERN = r"찬양|찬송|연습|특송"
STOPWORDS = {"the", "and", "for", "are", "you", "that", "this", "with", "not",
             "but", "his", "her", "our", "all", "one", "who", "has", "was"}


def parse_live(path):
    """Return a list of {time, ko, en} blocks from the app's transcript export."""
    text = open(path, encoding="utf-8").read().replace("\r", "")
    parts = re.split(r"\n## (\d\d:\d\d:\d\d)\n", text)
    blocks = []
    for i in range(1, len(parts), 2):
        body = parts[i + 1]
        m = re.search(r"\*\*Spoken[^\n]*\*\*\n(.*?)\n\n\*\*English\*\*\n(.*?)(?:\n\n---|\Z)",
                      body, re.S)
        if m:
            blocks.append({"time": parts[i], "ko": m.group(1).strip(), "en": m.group(2).strip()})
    return blocks


def clean_reference(section_body):
    s = re.sub(r"\*\([^)]*\)\*", "", section_body)          # stage directions *(...)*
    s = re.sub(r"\[[0-9:\s\-]+\]", "", s)                    # [mm:ss - mm:ss]
    s = re.sub(r"\*\*[^*]{0,40}:\*\*", "", s)                # **speaker:**
    s = re.sub(r"^\s*\*\s*\*\*[^*]+\*\*", "", s, flags=re.M)
    s = re.sub(r"^#.*$", "", s, flags=re.M)
    return s


def normalize(s):
    return re.sub(r"[^가-힣a-z0-9]", "", s.lower())


def to_codes(s):
    return np.frombuffer(s.encode("utf-32-le"), dtype=np.uint32)


def semiglobal_distance(ref, hyp_codes):
    """Edit distance of ref against its best-matching substring of hyp."""
    r = to_codes(ref)
    m = len(hyp_codes)
    cols = np.arange(m + 1, dtype=np.int32)
    prev = np.zeros(m + 1, dtype=np.int32)                   # free start in hyp
    for i, ch in enumerate(r, 1):
        cur = np.empty(m + 1, dtype=np.int32)
        cur[0] = i
        cur[1:] = np.minimum(prev[1:] + 1, prev[:-1] + (hyp_codes != ch))
        cur = np.minimum.accumulate(cur - cols) + cols       # insertions, vectorized
        prev = cur
    return int(prev.min())                                   # free end in hyp


def split_reference(reference_text, music_pattern):
    """Return (title, normalized_text, is_music) for each reference section."""
    if "\n## " in reference_text:
        out = []
        for sec in re.split(r"\n## ", reference_text)[1:]:
            title, _, body = sec.partition("\n")
            out.append((title.strip(), normalize(clean_reference(body)),
                        bool(re.search(music_pattern, title))))
        return out
    bins = re.split(r"\n# (\d+)분부터\n", reference_text)
    if len(bins) < 3:
        sys.exit("Unrecognized reference format: expected '## ' sections or '# N분부터' bins.")
    out = []
    for i in range(1, len(bins), 2):
        entries = re.findall(r"\*\*\[(\d\d:\d\d:\d\d)([^\]]*)\]\*\*(.*?)(?=\n\*\*\[|\n\[|\Z)",
                             bins[i + 1], re.S)
        speech = " ".join(re.sub(r"\[[^\]]*\]", "", e[2]) for e in entries if "찬양" not in e[1])
        text = normalize(speech)
        out.append((f"{bins[i]}분부터", text, len(text) < 50))   # no usable speech: treat as music
    return out


def score_sections(reference_text, live_ko, music_pattern):
    hyp = to_codes(normalize(live_ko))
    rows = []
    for title, ref, music in split_reference(reference_text, music_pattern):
        if not ref:
            continue
        dist = semiglobal_distance(ref, hyp)
        rows.append({"section": title, "ref_chars": len(ref), "errors": dist,
                     "cer": dist / len(ref), "music": music})
        print(f"  scored: {title[:50]}", file=sys.stderr)
    return rows


def check_facts(facts, live_ko, live_en):
    results = []
    for f in facts:
        def hits(patterns, text):
            return sorted({m.group(0) for p in patterns for m in re.finditer(p, text, re.I)})
        results.append({
            "id": f["id"],
            "ko_found": hits(f.get("ko", []), live_ko),
            "en_found": hits(f.get("en", []), live_en),
            "wrong_ko": hits(f.get("wrong_ko", []), live_ko),
            "wrong_en": hits(f.get("wrong_en", []), live_en),
            "note": f.get("note", ""),
        })
    return results


def english_phrase_check(reference_text, live_ko, live_en):
    start = reference_text.find("\n#")                      # skip any preamble before the first heading
    body = reference_text[start:] if start >= 0 else reference_text
    body = re.sub(r"`[^`]*`|\*\*|\[[^\]]*\]", "", body)
    phrases = []
    for p in re.findall(r"[A-Za-z][A-Za-z ,'\.\-]{3,}[A-Za-z]", body):
        p = p.strip()
        if p not in phrases:
            phrases.append(p)
    rows, total, in_ko, in_en = [], 0, 0, 0
    for p in phrases:
        words = [w for w in re.findall(r"[A-Za-z]+", p) if len(w) > 2 and w.lower() not in STOPWORDS]
        if not words:
            continue
        k = sum(bool(re.search(rf"\b{w}\b", live_ko, re.I)) for w in words)
        e = sum(bool(re.search(rf"\b{w}\b", live_en, re.I)) for w in words)
        rows.append({"phrase": p, "words": len(words), "in_ko_latin": k, "in_en": e})
        total, in_ko, in_en = total + len(words), in_ko + k, in_en + e
    return rows, total, in_ko, in_en


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("reference")
    ap.add_argument("live")
    ap.add_argument("--facts")
    ap.add_argument("--json")
    ap.add_argument("--music-pattern", default=MUSIC_PATTERN)
    a = ap.parse_args()

    reference = open(a.reference, encoding="utf-8").read().replace("\r", "")
    blocks = parse_live(a.live)
    if not blocks:
        sys.exit("No Spoken/English blocks found in the live transcript.")
    live_ko = " ".join(b["ko"] for b in blocks)
    live_en = " ".join(b["en"] for b in blocks)

    rows = score_sections(reference, live_ko, a.music_pattern)
    speech = [r for r in rows if not r["music"]]
    speech_cer = sum(r["errors"] for r in speech) / max(1, sum(r["ref_chars"] for r in speech))

    print(f"# Transcript score\n\nLive: {blocks[0]['time']} to {blocks[-1]['time']}, {len(blocks)} blocks\n")
    print("| CER | Ref chars | Section | Music |\n|---:|---:|---|:---:|")
    for r in rows:
        print(f"| {r['cer']:.1%} | {r['ref_chars']} | {r['section']} | {'x' if r['music'] else ''} |")
    print(f"\n**Speech-only CER: {speech_cer:.1%}** "
          f"({sum(r['ref_chars'] for r in speech)} reference characters)\n")

    out = {"sections": rows, "speech_only_cer": speech_cer}

    if a.facts:
        facts = json.load(open(a.facts, encoding="utf-8"))
        fr = check_facts(facts, live_ko, live_en)
        print("## Key facts\n\n| Fact | Korean found | English found | Wrong variants | Note |\n|---|---|---|---|---|")
        for f in fr:
            wrong = ", ".join(f["wrong_ko"] + f["wrong_en"])
            print(f"| {f['id']} | {', '.join(f['ko_found']) or 'MISSING'} | "
                  f"{', '.join(f['en_found']) or 'MISSING'} | {wrong} | {f['note']} |")
        out["facts"] = fr

    er, total, in_ko, in_en = english_phrase_check(reference, live_ko, live_en)
    if total:
        print(f"\n## English phrases in reference (rough indicator)\n\n"
              f"Content words kept in Latin script in Korean source: {in_ko}/{total} ({in_ko/total:.0%}); "
              f"present in English output: {in_en}/{total} ({in_en/total:.0%})\n")
        out["english_phrases"] = er

    if a.json:
        json.dump(out, open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
