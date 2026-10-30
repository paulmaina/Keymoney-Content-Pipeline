"""Unit tests for make_video.py. None of these need ffmpeg or network access."""
from pathlib import Path

import pytest

import make_video as mv
from make_video import Word


def words(text, step=0.5):
    return [Word(t, i * step, i * step + step * 0.9) for i, t in enumerate(text.split())]


# ---------- parse_script ----------

def test_parse_script_scenes_visuals_and_comments(tmp_path):
    p = tmp_path / "s.txt"
    p.write_text("# a comment\nHello there.\nSecond line.\n\n"
                 "[visual: clip.mp4]\nScene two.\n\n\n"
                 "# only a comment\n\n"
                 "[VISUAL:  pic one.png ]\nScene three.\n", encoding="utf-8")
    assert mv.parse_script(p) == [
        {"text": "Hello there. Second line.", "visual": None, "video": False, "description": ""},
        {"text": "Scene two.", "visual": "clip.mp4", "video": False, "description": ""},
        {"text": "Scene three.", "visual": "pic one.png", "video": False, "description": ""},
    ]


def test_comments_are_dropped_and_visuals_lines_become_the_description(tmp_path):
    p = tmp_path / "s.txt"
    p.write_text("# HACK 1 - Visuals: market in harsh sun\n#\nSpoken one.\n   # indented note\nSpoken two.\n"
                 "#no space after the hash\n# Shot: close-up of the pots\n", encoding="utf-8")
    assert mv.parse_script(p) == [{"text": "Spoken one. Spoken two.", "visual": None, "video": False,
                                   "description": "HACK 1 - Visuals: market in harsh sun; Shot: close-up of the pots"}]
    assert [n for n, _ in mv.parse_comments(p)] == [2, 4, 6]  # plain comments only


def test_only_labelled_lines_are_descriptions(tmp_path):
    p = tmp_path / "s.txt"
    p.write_text('# The "Visuals" notes say what footage each scene needs.\n# note: from the doc\n'
                 "# Visuals:\nSpoken.\n", encoding="utf-8")  # no label, a different label, an empty label
    assert mv.parse_script(p)[0]["description"] == ""


def test_parse_script_ignores_utf8_bom(tmp_path):
    p = tmp_path / "s.txt"
    p.write_bytes("﻿# comment\nSpoken text.".encode("utf-8"))
    assert mv.parse_script(p) == [{"text": "Spoken text.", "visual": None, "video": False, "description": ""}]


def test_parse_script_empty(tmp_path):
    p = tmp_path / "s.txt"
    p.write_text("# nothing to say\n\n", encoding="utf-8")
    assert mv.parse_script(p) == []


# ---------- srt_time ----------

@pytest.mark.parametrize("t, expected", [
    (0, "00:00:00,000"),
    (1.5, "00:00:01,500"),
    (61.0004, "00:01:01,000"),
    (3725.678, "01:02:05,678"),
    (59.9996, "00:01:00,000"),
])
def test_srt_time(t, expected):
    assert mv.srt_time(t) == expected


# ---------- chunking ----------

def texts(chunks):
    return [" ".join(w.text for w in c) for c in chunks]


def test_chunk_breaks_on_punctuation():
    assert texts(mv.chunk_words(words("Hi there, friend. How are you?"), 6)) == \
        ["Hi there,", "friend.", "How are you?"]


def test_chunk_respects_max_words():
    assert texts(mv.chunk_words(words("one two three four five six seven eight"), 3)) == \
        ["one two three", "four five six", "seven eight"]


def test_chunk_avoids_lone_sentence_end():
    # without the one-word allowance this leaves "studio." on its own line
    assert texts(mv.chunk_words(words("Welcome to your new AI video studio."), 6)) == \
        ["Welcome to your new AI video studio."]


def test_chunk_keeps_timings():
    chunks = mv.chunk_words(words("a b. c"), 6)
    assert chunks[0][0].start == 0 and chunks[1][0].text == "c"


# ---------- alignment ----------

def test_align_uses_script_spelling_and_punctuation():
    heard = words("hello my name is kemani and i love brandco")
    out = mv.align_words(heard, "Hello, my name is Kimani, and I love BrandCo!")
    assert [w.text for w in out] == ["Hello,", "my", "name", "is", "Kimani,", "and", "I", "love", "BrandCo!"]
    assert [(w.start, w.end) for w in out] == [(w.start, w.end) for w in heard]


def test_align_unequal_replace_spreads_script_words_over_span():
    heard = words("it is twenty twenty six now")
    out = mv.align_words(heard, "It is 2026 now.")
    assert [w.text for w in out] == ["It", "is", "2026", "now."]
    assert out[2].start == heard[2].start and out[2].end == pytest.approx(heard[4].end)


def test_spread_splits_time_by_length():
    out = mv.spread(["ab", "abcdef"], 0.0, 8.0)
    assert [(w.start, w.end) for w in out] == [(0.0, 2.0), (2.0, 8.0)]


def test_align_falls_back_to_whisper_for_unscripted_words():
    heard = words("hello world thank you for watching")
    out = mv.align_words(heard, "Hello world.")
    assert [w.text for w in out] == ["Hello", "world.", "thank", "you", "for", "watching"]


def test_align_attaches_missed_script_words():
    heard = words("the quick fox")
    out = mv.align_words(heard, "The quick brown fox.")
    assert [w.text for w in out] == ["The", "quick brown", "fox."]


def test_align_attaches_missed_leading_words():
    heard = words("world peace")
    out = mv.align_words(heard, "Hello world peace.")
    assert [w.text for w in out] == ["Hello world", "peace."]
    assert out[0].start == heard[0].start


def test_align_keeps_dashes_with_previous_word():
    out = mv.align_words(words("wait for it"), "Wait — for it.")
    assert [w.text for w in out] == ["Wait —", "for", "it."]


def test_align_handles_empty_input():
    assert mv.align_words([], "Some text") == []
    heard = words("some text")
    assert mv.align_words(heard, "") == heard


# ---------- small helpers ----------

def test_each_video_gets_a_folder_named_after_the_script_or_o(tmp_path):
    import outputs
    script = tmp_path / "Promo.txt"
    out = outputs.Outputs.for_script(None, script)
    assert out.folder == mv.ROOT / "Exports" / "Promo" and out.name == "Promo"
    assert outputs.Outputs.for_script(Path("Launch"), script).folder == mv.ROOT / "Exports" / "Launch"  # a plain name
    assert outputs.Outputs.for_script(tmp_path / "videos" / "Final", script).folder == tmp_path / "videos" / "Final"
    assert out.video("horizontal") == out.upload / "Promo.mp4" and out.short(2) == out.upload / "Promo_Short_Part2.mp4"
    assert (out.thumbnail.name, out.youtube.name) == ("thumbnail.jpg", "youtube.md") and out.thumbnail.parent == out.upload
    assert {p.parent for p in (out.prompts, out.media, out.checks, out.srt, out.short_srt(1), out.log)} == {out.working}


def test_vertical_flag_is_alias_for_format():
    assert mv.parse_args(["s.txt"]).format == "both"
    assert mv.parse_args(["s.txt", "--vertical"]).format == "vertical"


def test_title_card_text_shrinks_instead_of_truncating():
    text = " ".join(["Lots of narration words here."] * 40)
    font, wrapped, size = mv.fit_text(text, mv.DEFAULT_FONT, 1920, 1080)
    assert wrapped.replace("\n", " ") == " ".join(text.split())
    assert size < 1080 // 16
