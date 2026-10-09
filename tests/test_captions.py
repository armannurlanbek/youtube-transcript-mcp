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
