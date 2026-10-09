from datetime import date

from ytmcp.render import Paragraph, format_clock, paragraphs, to_markdown, watch_url
from ytmcp.youtube import Fetched, Track

VID = "aB3_-xYz012"


def fetched(**over):
    base = dict(video_id=VID, title="Building a Birdhouse", channel="Shop Talk",
                duration_seconds=9667, published="2026-10-01",
                track=Track(code="en-orig", language="en", kind="auto", url="u"), vtt="")
    base.update(over)
    return Fetched(**base)


def test_format_clock():
    assert format_clock(0) == "0:00"
    assert format_clock(47.9) == "0:47"
    assert format_clock(605) == "10:05"
    assert format_clock(3600) == "1:00:00"
    assert format_clock(9667) == "2:41:07"


def test_watch_url():
    assert watch_url(VID) == f"https://www.youtube.com/watch?v={VID}"
    assert watch_url(VID, 47.9) == f"https://www.youtube.com/watch?v={VID}&t=47s"


def test_speaker_mark_starts_paragraph_and_is_dropped():
    cues = [(0.0, "hello there."), (2.0, ">> hi, how are you"), (4.0, "fine")]
    assert paragraphs(cues) == [Paragraph(0.0, "hello there."), Paragraph(2.0, "hi, how are you fine")]


def test_speaker_mark_mid_line_splits():
    cues = [(0.0, "so that's it. >> right >> yes")]
    assert paragraphs(cues) == [Paragraph(0.0, "so that's it."), Paragraph(0.0, "right"), Paragraph(0.0, "yes")]


def test_bare_speaker_mark_line():
    cues = [(0.0, "one."), (3.0, ">>"), (4.0, "two")]
    assert paragraphs(cues) == [Paragraph(0.0, "one."), Paragraph(4.0, "two")]


def test_soft_break_waits_for_sentence_end():
    cues = [(0.0, "a"), (30.0, "b"), (46.0, "c without end"), (50.0, "d."), (55.0, "e")]
    assert paragraphs(cues) == [Paragraph(0.0, "a b c without end d."), Paragraph(55.0, "e")]


def test_hard_break_at_90_seconds():
    cues = [(0.0, "a"), (60.0, "b"), (90.0, "c"), (100.0, "d")]
    assert paragraphs(cues) == [Paragraph(0.0, "a b"), Paragraph(90.0, "c d")]


def test_whitespace_collapsed():
    assert paragraphs([(0.0, "  a   b "), (1.0, "c")]) == [Paragraph(0.0, "a b c")]


def test_empty():
    assert paragraphs([]) == []


def test_to_markdown_full():
    md = to_markdown(fetched(), [(0.0, "hello."), (47.5, ">> next part")], transcribed=date(2026, 10, 9))
    assert md == (
        "# Building a Birdhouse\n\n"
        "- **Channel:** Shop Talk\n"
        f"- **Video:** https://www.youtube.com/watch?v={VID}\n"
        "- **Length:** 2:41:07\n"
        "- **Published:** 2026-10-01\n"
        "- **Language:** en (auto-generated captions)\n"
        "- **Transcribed:** 2026-10-09\n\n"
        "---\n\n"
        f"[0:00](https://www.youtube.com/watch?v={VID}&t=0s) hello.\n\n"
        f"[0:47](https://www.youtube.com/watch?v={VID}&t=47s) next part\n"
    )


def test_to_markdown_missing_fields_and_odd_title():
    md = to_markdown(fetched(title="# Big\nNews <b>", channel="", duration_seconds=None, published=None,
                             track=Track("ru", "ru", "translated", "u")),
                     [(0.0, "x")], transcribed=date(2026, 10, 9))
    assert md.startswith("# \\# Big News <b>\n\n")
    assert "Length" not in md and "Published" not in md and "Channel" not in md
    assert "- **Language:** ru (auto-translated captions)\n" in md
