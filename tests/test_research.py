"""Unit tests for research.py. No network: search pages and the transcript API are faked."""
import json
import urllib.error
from types import SimpleNamespace

import pytest

import research


def test_slug():
    assert research.slug("Water storage: village hacks!") == "water-storage-village-hacks"
    assert research.slug("???") == "topic"


@pytest.mark.parametrize("text", ["dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=3",
                                  "https://youtu.be/dQw4w9WgXcQ?si=x", "youtube.com/shorts/dQw4w9WgXcQ",
                                  "https://www.youtube.com/embed/dQw4w9WgXcQ"])
def test_video_id(text):
    assert research.video_id(text) == "dQw4w9WgXcQ"


def test_video_id_rejects_other_links():
    assert research.video_id("https://example.com/watch") is None
    assert research.video_id("short") is None


@pytest.mark.parametrize("text, n", [("1,234,567 views", 1234567), ("1.2M views", 1200000), ("15K views", 15000),
                                     ("No views", 0), ("", None), ("views", None)])
def test_parse_views(text, n):
    assert research.parse_views(text) == n


def renderer(vid, title, views="1,000 views"):
    return {"videoRenderer": {"videoId": vid, "title": {"runs": [{"text": title}]},
                              "ownerText": {"runs": [{"text": "Chan"}]}, "viewCountText": {"simpleText": views},
                              "lengthText": {"simpleText": "10:01"}, "publishedTimeText": {"simpleText": "1 year ago"}}}


def page(*renderers):
    data = {"contents": {"sections": [{"items": list(renderers)}, {"shelf": {"items": [renderers[0]]}}]}}
    return f"<script>var ytInitialData = {json.dumps(data)};</script>"


def test_parse_results_reads_videos_once_in_order():
    vids = research.parse_results(page(renderer("aaaaaaaaaaa", "First"), renderer("bbbbbbbbbbb", "Second", "2M views"),
                                       {"reelItemRenderer": {"videoId": "ccccccccccc"}}))
    assert [v["id"] for v in vids] == ["aaaaaaaaaaa", "bbbbbbbbbbb"]  # Shorts skipped, repeats dropped
    assert vids[1] == {"id": "bbbbbbbbbbb", "url": "https://www.youtube.com/watch?v=bbbbbbbbbbb", "title": "Second",
                       "channel": "Chan", "views": 2000000, "views_text": "2M views", "length": "10:01",
                       "published": "1 year ago"}


def test_parse_results_on_a_changed_page():
    assert research.parse_results("<html>nothing here</html>") == []
    assert research.parse_results("var ytInitialData = {broken;</script>") == []


def test_search_tops_up_from_query_variants():
    pages = {"water": [{"id": "a" * 11}, {"id": "b" * 11}], "water ideas": [{"id": "b" * 11}, {"id": "c" * 11}]}
    seen = []

    def fake(query):
        seen.append(query)
        return pages.get(query, [])
    vids = research.search("water", 3, page=fake)
    assert [v["id"][0] for v in vids] == ["a", "b", "c"] and seen == ["water", "water ideas"]
    assert vids[2]["query"] == "water ideas"


def test_search_keeps_results_when_a_later_query_fails():
    def fake(query):
        if query != "water":
            raise urllib.error.URLError("offline")
        return [{"id": "a" * 11}]
    assert len(research.search("water", 10, page=fake)) == 1


# ---------- transcripts ----------

class Errors:
    class CouldNotRetrieveTranscript(Exception):
        pass

    class RequestBlocked(CouldNotRetrieveTranscript):
        pass

    class IpBlocked(RequestBlocked):
        pass

    class NoTranscriptFound(CouldNotRetrieveTranscript):
        pass

    class TranscriptsDisabled(CouldNotRetrieveTranscript):
        pass

    class VideoUnavailable(CouldNotRetrieveTranscript):
        pass

    VideoUnplayable = AgeRestricted = InvalidVideoId = VideoUnavailable


def transcript(text, lang="en", generated=False):
    return SimpleNamespace(snippets=[SimpleNamespace(text=t) for t in text.split("|")], language_code=lang,
                           is_generated=generated)


class FakeApi:
    def __init__(self, behaviour):
        self.behaviour, self.calls = behaviour, []

    def fetch(self, vid, languages=None):
        self.calls.append(vid)
        b = self.behaviour[vid]
        if isinstance(b, type):
            raise b()
        return b

    def list(self, vid):
        return [SimpleNamespace(fetch=lambda: transcript("hola mundo", "es"))]


def videos(*ids):
    return [{"id": i, "title": f"t{i}"} for i in ids]


def test_fetch_records_status_and_saves_text(tmp_path):
    api = FakeApi({"a" * 11: transcript("[Music] >> Hello  there|world", generated=True),
                   "b" * 11: Errors.TranscriptsDisabled, "c" * 11: Errors.NoTranscriptFound,
                   "d" * 11: Errors.VideoUnavailable})
    vs = videos("a" * 11, "b" * 11, "c" * 11, "d" * 11)
    assert research.fetch_transcripts(vs, tmp_path, api=api, yta=Errors, pause=0) is False
    assert [v["transcript"] for v in vs] == ["ok (auto captions)", "no captions", "ok",
                                             "unavailable (VideoUnavailable)"]
    assert (tmp_path / ("a" * 11 + ".txt")).read_text(encoding="utf-8") == "Hello there world"
    assert vs[2]["language"] == "es" and vs[0]["words"] == 3
    assert not (tmp_path / ("b" * 11 + ".txt")).exists()


def test_an_ip_block_stops_fetching_and_keeps_the_rest_marked(tmp_path, capsys):
    api = FakeApi({"a" * 11: transcript("one"), "b" * 11: Errors.IpBlocked, "c" * 11: transcript("never")})
    vs = videos("a" * 11, "b" * 11, "c" * 11)
    assert research.fetch_transcripts(vs, tmp_path, api=api, yta=Errors, pause=0) is True
    assert [v["transcript"] for v in vs] == ["ok", "blocked", "blocked"]
    assert api.calls == ["a" * 11, "b" * 11]
    assert "blocking" in capsys.readouterr().out


def test_saved_transcripts_are_reused(tmp_path):
    (tmp_path / ("a" * 11 + ".txt")).write_text("saved words here", encoding="utf-8")
    api = FakeApi({})
    vs = videos("a" * 11)
    research.fetch_transcripts(vs, tmp_path, api=api, yta=Errors, pause=0)
    assert api.calls == [] and vs[0]["transcript"] == "ok (saved earlier)" and vs[0]["words"] == 3


def test_missing_library_still_keeps_titles(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(research, "load_api", lambda: None)
    vs = videos("a" * 11)
    assert research.fetch_transcripts(vs, tmp_path) is False
    assert vs[0]["transcript"].startswith("skipped") and "pip install" in capsys.readouterr().out


def test_title_patterns():
    p = research.title_patterns(["5 Water Hacks You NEED", "How to store water?", "Free water | DIY", "Rain water tanks"])
    assert p["devices"] == {"a number": 1, "a question": 1, '"how to"': 1, "an ALL-CAPS word": 2,
                            "a | or - separator": 1}
    assert p["words"][0] == ("water", 4)
