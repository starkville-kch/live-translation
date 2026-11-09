import asyncio

from app.broadcast import CaptionBroadcaster
from app.text_corrections import NameCorrector, collapse_repetitions


def test_name_pairs_are_longest_first_and_english_whole_words():
    corrector = NameCorrector()
    assert corrector.correct("스타필드 한인 교회, 스타필드 교회, 카미카제 자매") == "스타크빌 한인교회, 스타크빌 한인교회, 카미카 자매"
    assert corrector.correct("Starfield Korean Church; Starfield Church; Starfield; Kamikaze sister") == "Starkville Korean Church; Starkville Korean Church; Starkville; Kamika"
    assert corrector.correct("Starfields and MyStarfieldChurch") == "Starfields and MyStarfieldChurch"


def test_repeated_words_and_phrases_collapse_only_after_four():
    assert collapse_repetitions("주옵소서 " * 4).strip() == ("주옵소서 " * 4).strip()
    assert collapse_repetitions("주옵소서 " * 10).strip() == "주옵소서"
    assert collapse_repetitions("주옵소서" * 10) == "주옵소서"
    assert collapse_repetitions("Please help us " * 5).strip() == "Please help us"
    for text in ("11111", "baaaaad", "aaaaa@example.com", "2026 2026 2026 2026 2026"):
        assert collapse_repetitions(text) == text


def test_commit_corrects_both_display_languages_without_annotations():
    async def run():
        broadcaster = CaptionBroadcaster()
        queue = broadcaster.add_client()
        broadcaster.on_source_delta("스타필드 한인 교회 카미카제 자매")
        broadcaster.on_caption_delta("Starfield Korean Church; Kamikaze sister")
        while True:
            event = await asyncio.wait_for(queue.get(), timeout=3)
            if event.kind == "commit":
                assert event.source == "스타크빌 한인교회 카미카 자매"
                assert event.target == "Starkville Korean Church; Kamika"
                break
        broadcaster.remove_client(queue)
    asyncio.run(run())


def test_hidden_captions_and_confirmed_audio_mute_are_independent():
    async def run():
        broadcaster = CaptionBroadcaster()
        captions = broadcaster.add_client()
        audio = broadcaster.add_audio_client()
        broadcaster.set_output_visibility(True, False)
        assert captions.get_nowait().kind == "hidden"
        broadcaster.on_caption_delta("unexpected")
        broadcaster.on_source_delta("unexpected")
        assert captions.empty()
        broadcaster.on_audio_chunk(b"first")
        assert audio.get_nowait() == b"first"
        broadcaster.on_audio_chunk(b"queued")
        broadcaster.set_output_visibility(True, True)
        assert audio.empty()
        assert captions.get_nowait().kind == "audio_clear"
        broadcaster.on_audio_chunk(b"muted")
        assert audio.empty()
        broadcaster.set_output_visibility(False, False)
        assert captions.get_nowait().kind == "visible"
        broadcaster.on_audio_chunk(b"restored")
        assert audio.get_nowait() == b"restored"
    asyncio.run(run())
