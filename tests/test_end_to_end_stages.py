"""Tests for upstream stages (research, script writer, fact-check) and unified pipeline CLI."""
import json
from pathlib import Path

import make_video as mv
import produce
import research as yt_research
import script_check
from video_producer import fact_check, pipeline, research_stage, script_writer

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "Scripts" / "TEMPLATE.txt"


def test_research_stage_writes_report_and_raw_data(tmp_path, monkeypatch):
    fake_videos = [
        {"id": "aaaaaaaaaaa", "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa",
         "title": "5 Village Water Hacks", "channel": "EcoLab", "views": 50000,
         "views_text": "50K views", "length": "8:10", "published": "1 year ago"},
        {"id": "bbbbbbbbbbb", "url": "https://www.youtube.com/watch?v=bbbbbbbbbbb",
         "title": "How Sand Dams Work", "channel": "WaterOrg", "views": 12000,
         "views_text": "12K views", "length": "5:00", "published": "2 years ago"},
    ]
    monkeypatch.setattr(yt_research, "search", lambda topic, want: list(fake_videos))
    monkeypatch.setattr(yt_research, "fill_titles", lambda videos: None)

    def fake_fetch(videos, out_dir, refresh=False):
        for v in videos:
            v["transcript"] = "ok"
            (out_dir / f"{v['id']}.txt").write_text("transcript words here", encoding="utf-8")
        return False

    monkeypatch.setattr(yt_research, "fetch_transcripts", fake_fetch)

    res = research_stage.run_research(
        "village water hacks",
        max_videos=5,
        raw_dir=tmp_path / "raw",
        out_dir=tmp_path / "Research",
        synthesize=False,
    )
    assert res["report_path"].exists()
    text = res["report_path"].read_text(encoding="utf-8")
    assert "# Research: village water hacks" in text
    assert "5 Village Water Hacks" in text
    assert (res["raw_dir"] / "videos.json").exists()


def test_script_writer_fallback_passes_check(tmp_path):
    out_script = tmp_path / "cooling.txt"
    out_json = tmp_path / "cooling.json"
    path, doc = script_writer.generate_script(
        "Passive clay cooling",
        channel_id="smart_village_boy",
        out_path=out_script,
        json_path=out_json,
        providers=None,
    )
    assert path.exists() and out_json.exists()
    assert doc.channel == "smart_village_boy"
    assert "Smart Village Boy" in doc.character_sheet
    args = mv.parse_args([str(path), "-o", str(tmp_path / "exp")])
    analysis = script_check.analyse(path, args)
    assert analysis["flags"] == []


def test_script_writer_llm_normalizes_long_scenes_and_passes_check(tmp_path):
    long_narration = (
        "First sentence explains the hot desert sun beating down on bare clay soil every single afternoon. "
        "Second sentence describes how villagers collect fallen palm leaves and wet sand before noon arrives. "
        "Third sentence shows how water evaporates steadily from the outer porous vessel wall. "
        "Fourth sentence measures the drop inside the shaded storage chamber over twenty four hours. "
        "Fifth sentence notes that fresh produce keeps far longer without any power grid."
    )
    payload = {
        "title": "Desert Cooling With Two Clay Pots",
        "characters": [],
        "scenes": [
            {"section": "HOOK", "location": "Kano, Nigeria", "visual_description": "Smart Village Boy kneeling beside clay pots at dawn.", "is_video_motion": True, "narration": long_narration},
            {"section": "HOOK", "visual_description": "Smart Village Boy beside a wooden globe.", "is_video_motion": False, "narration": "Welcome to Smart Village Boy. Stay to the end for a desert cooler."},
            {"section": "HACK 1 NIGERIA", "visual_description": "Hands placing a small pot inside a big pot.", "is_video_motion": True, "narration": "In northern Nigeria, two clay pots cool vegetables without power. Wet sand fills the gap between them."},
            {"section": "HACK 1 NIGERIA", "visual_description": "Fresh red tomatoes inside a cool clay pot.", "is_video_motion": False, "narration": "As water evaporates, it draws heat out of the inner pot. Produce stays crisp for days longer."},
            {"section": "OUTRO", "visual_description": "Smart Village Boy smiling beside the globe at sunset.", "is_video_motion": False, "narration": "Two pots, wet sand, and simple physics keep food fresh. Which idea would you try first?"},
            {"section": "OUTRO", "visual_description": "Smart Village Boy waving at dusk.", "is_video_motion": False, "narration": "Tell us in the comments, and subscribe for more clever village ideas."},
        ],
    }

    class FakeProviders:
        text_ok = True

        def text(self, messages, **kwargs):
            return json.dumps(payload)

    out_script = tmp_path / "llm_script.txt"
    path, doc = script_writer.generate_script(
        "Desert clay cooling",
        out_path=out_script,
        providers=FakeProviders(),
    )
    assert len(doc.scenes) > 6  # the over-long scene was split cleanly
    args = mv.parse_args([str(path), "-o", str(tmp_path / "exp")])
    analysis = script_check.analyse(path, args)
    assert analysis["flags"] == []


def test_fact_check_extracts_claims_without_editing_script(tmp_path):
    before = TEMPLATE.read_text(encoding="utf-8")
    out_md = tmp_path / "template_factcheck.md"
    path, claims = fact_check.run_fact_check(TEMPLATE, out_path=out_md)
    assert path == out_md and path.exists()
    assert len(claims) >= 10
    text = path.read_text(encoding="utf-8")
    assert "# Fact-check: TEMPLATE" in text
    assert "## Checklist" in text
    assert TEMPLATE.read_text(encoding="utf-8") == before


def test_produce_fact_check_missing_script_suggests_underscore_match(tmp_path):
    import pytest
    existing = tmp_path / "water_storage.txt"
    existing.write_text("# Title\n\nHello.\n", encoding="utf-8")
    missing = tmp_path / "water-storage.txt"
    with pytest.raises(SystemExit) as exc:
        produce.main(["fact-check", str(missing)])
    assert "Script not found:" in str(exc.value)
    assert "Did you mean:" in str(exc.value)


def test_research_cleans_newlines_in_table_and_script_writer_keeps_exact_host(tmp_path, monkeypatch):
    monkeypatch.setattr(yt_research, "search", lambda topic, want: [
        {"id": "ddddddddddd", "url": "https://youtu.be/ddddddddddd", "title": "Line1\nLine2",
         "channel": "Chan\n\nSub | Pipe", "views": 100, "views_text": "100 views", "length": "1:00", "published": "1d"}
    ])
    monkeypatch.setattr(yt_research, "fill_titles", lambda videos: None)
    monkeypatch.setattr(yt_research, "fetch_transcripts", lambda videos, out_dir, refresh=False: False)
    res = research_stage.run_research("clean table", raw_dir=tmp_path / "raw", out_dir=tmp_path / "res", synthesize=False)
    md = res["report_path"].read_text(encoding="utf-8")
    assert "| 1 | Line1 Line2 | Chan Sub / Pipe |" in md

    class FakeProv:
        text_ok = True

        def text(self, *a, **k):
            return json.dumps({
                "title": "Exact Host Test Title",
                "characters": [{"name": "Smart Village Boy", "description": "shortened wrong desc"}],
                "scenes": [
                    {"section": "HOOK", "location": "Rural Kenya, Kenya", "visual_description": "Smart Village Boy.", "narration": "One."},
                    {"section": "OUTRO", "visual_description": "Smart Village Boy.", "narration": "Two."},
                ],
            })

    _, doc = script_writer.generate_script("test host", out_path=tmp_path / "s.txt", providers=FakeProv())
    assert doc.character_sheet["Smart Village Boy"] == script_writer.SMART_VILLAGE_HOST.description
    assert doc.scenes[0].location == "Rural Kenya"


def test_pipeline_end_to_end_to_stage_check_and_cli_export_plan(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(script_writer, "SCRIPTS_DIR", tmp_path / "Scripts")
    monkeypatch.setattr(fact_check, "RESEARCH_DIR", tmp_path / "Research")
    monkeypatch.setattr(research_stage, "RESEARCH_DIR", tmp_path / "Research")
    monkeypatch.setattr(yt_research, "RAW_DIR", tmp_path / "Research" / "_raw")
    monkeypatch.setattr(yt_research, "search", lambda topic, want: [
        {"id": "ccccccccccc", "url": "https://youtu.be/ccccccccccc", "title": "Solar Dryers",
         "channel": "Demo", "views": 1000, "views_text": "1K views", "length": "4:00", "published": "1y"}
    ])
    monkeypatch.setattr(yt_research, "fill_titles", lambda videos: None)
    monkeypatch.setattr(yt_research, "fetch_transcripts", lambda videos, out_dir, refresh=False: False)

    rep = pipeline.run_end_to_end(
        topic="Solar food dryers",
        do_research=True,
        do_fact_check=True,
        to_stage="check",
        use_llm=False,
        forward_args=["-o", str(tmp_path / "Exports" / "solar")],
    )
    assert rep.stages_completed == ["research", "script", "fact-check", "check"]
    assert rep.research_report and rep.research_report.exists()
    assert rep.script_path and rep.script_path.exists()
    assert rep.fact_check_report and rep.fact_check_report.exists()
    assert rep.flags == []

    plan_json = tmp_path / "exported_plan.json"
    rc = produce.main(["export-plan", str(rep.script_path), "-o", str(plan_json)])
    assert rc == 0 and plan_json.exists()
    loaded = json.loads(plan_json.read_text(encoding="utf-8"))
    assert len(loaded["scenes"]) >= 6
