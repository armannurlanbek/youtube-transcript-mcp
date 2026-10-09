from pathlib import Path

from ytmcp.captions import parse_vtt

FIX = Path(__file__).parent / "fixtures"


def test_auto_captions_rolling_repeats_removed():
    cues = parse_vtt((FIX / "auto_captions.vtt").read_text(encoding="utf-8"))
    assert cues == [
        (0.16, "welcome back to the workshop"),
        (2.879, "today we build a birdhouse."),
        (6.0, ">> sounds great & simple"),
    ]


def test_manual_captions_keep_timings_and_text():
    cues = parse_vtt((FIX / "manual_captions.vtt").read_text(encoding="utf-8"))
    assert cues == [
        (1.0, "Measure twice,"),
        (3.5, "cut once."),
        (3723.25, "That's the rule --> always."),
        (3725.0, "cut once."),
    ]


def test_empty_and_header_only():
    assert parse_vtt("") == []
    assert parse_vtt("WEBVTT\nKind: captions\nLanguage: en\n") == []


def test_spoken_lines_that_look_like_headers_are_kept():
    vtt = ("WEBVTT\nKind: captions\nLanguage: en\n\n"
           "00:00:01.000 --> 00:00:02.000\nLanguage: it matters\n\n"
           "00:00:02.000 --> 00:00:03.000\n2024\n\n"
           "7\n00:00:03.000 --> 00:00:04.000\nwas the year\n")
    assert parse_vtt(vtt) == [(1.0, "Language: it matters"), (2.0, "2024"), (3.0, "was the year")]


def test_malformed_timing_keeps_previous_start():
    vtt = "WEBVTT\n\n00:00:05.000 --> 00:00:06.000\nfirst\n\n00:xx:01.000 --> 00:00:02.000\nsecond\n"
    assert parse_vtt(vtt) == [(5.0, "first"), (5.0, "second")]


def test_bare_number_cue_without_id_is_kept():
    vtt = ("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n2024\n\n"
           "00:00:02.000 --> 00:00:03.000\nwas the year\n")
    assert parse_vtt(vtt) == [(1.0, "2024"), (2.0, "was the year")]


def test_many_digit_lines_parse_in_linear_time():
    import time

    body = "\n".join(str(i) for i in range(200_000))
    vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n" + body + "\n"
    began = time.perf_counter()
    cues = parse_vtt(vtt)
    assert time.perf_counter() - began < 1.0
    assert len(cues) == 200_000


def test_spoken_arrow_line_is_text_and_keeps_start():
    vtt = ("WEBVTT\n\n00:00:05.000 --> 00:00:06.000\n3:00 --> the end\n\n"
           "00:00:07.000 --> 00:00:08.000\nnext\n")
    assert parse_vtt(vtt) == [(5.0, "3:00 --> the end"), (7.0, "next")]


def test_spoken_line_starting_with_note_is_kept():
    vtt = ("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nNOTE that matters\n\n"
           "NOTE a comment\n\n00:00:03.000 --> 00:00:04.000\nok\n")
    assert parse_vtt(vtt) == [(1.0, "NOTE that matters"), (3.0, "ok")]


def test_comma_millisecond_separator():
    vtt = "WEBVTT\n\n00:00:01,500 --> 00:00:02,000\nhello\n"
    assert parse_vtt(vtt) == [(1.5, "hello")]
