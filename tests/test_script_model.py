"""Tests for video_producer.script_model: parsing, round-trip formatting, and JSON plans."""
from pathlib import Path

import make_video as mv
import script_check
from video_producer.script_model import (
    CharacterSpec,
    SceneSpec,
    ScriptDocument,
    export_script_json,
    format_script_txt,
    load_script_json,
    parse_script_document,
)

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "Scripts" / "TEMPLATE.txt"


def test_parse_template_into_structured_document():
    doc = parse_script_document(TEMPLATE)
    assert doc.title == "Five Village Water Hacks That Beat Drought"
    assert doc.channel == "smart_village_boy"
    assert "Smart Village Boy" in doc.character_sheet
    assert len(doc.scenes) == len(mv.parse_script(TEMPLATE))
    assert doc.scenes[0].section == "HOOK"
    assert doc.scenes[0].is_video_motion is True
    assert doc.scenes[-1].section == "OUTRO"
    assert any(s.media == "stock" for s in doc.scenes)
    assert any(s.media == "ai" for s in doc.scenes)
    assert any(s.location == "Kitui, Kenya" for s in doc.scenes)


def test_roundtrip_format_and_json_export(tmp_path):
    original = ScriptDocument(
        title="How Clay Pots Cool Vegetables",
        channel="smart_village_boy",
        characters=[CharacterSpec("Smart Village Boy", "Kenyan boy, twelve years old, red cap, yellow T-shirt")],
        notes=["Test note"],
        scenes=[
            SceneSpec(1, "No fridge in the desert heat. Yet tomatoes stay fresh.", "Smart Village Boy holding a clay pot at sunrise.", is_video_motion=True, section="HOOK"),
            SceneSpec(2, "Welcome to Smart Village Boy. Stay to the end for a simple trick.", "Smart Village Boy beside a wooden globe.", section="HOOK"),
            SceneSpec(3, "In northern Nigeria, heat spoils crops quickly.", "A Nigerian vendor at an open market at midday.", location="Kano, Nigeria", media="ai", section="HACK 1 NIGERIA"),
            SceneSpec(4, "Two clay pots with wet sand cool the inner pot by evaporation.", "Close view of hands pouring wet sand between two clay pots.", section="HACK 1 NIGERIA"),
            SceneSpec(5, "Evaporation pulls heat away without electricity.", "Clay pot cooler shaded under a cloth.", section="OUTRO"),
            SceneSpec(6, "Which idea would you try? Subscribe for more.", "Smart Village Boy waving at dusk.", section="OUTRO"),
        ],
    )
    txt_path = tmp_path / "roundtrip.txt"
    txt_path.write_text(format_script_txt(original), encoding="utf-8")

    reparsed = parse_script_document(txt_path)
    assert reparsed.title == original.title
    assert reparsed.channel == original.channel
    assert reparsed.character_sheet == original.character_sheet
    assert len(reparsed.scenes) == len(original.scenes)
    assert [s.text for s in reparsed.scenes] == [s.text for s in original.scenes]
    assert reparsed.scenes[2].location == "Kano, Nigeria"
    assert reparsed.scenes[2].media == "ai"

    args = mv.parse_args([str(txt_path), "-o", str(tmp_path / "out")])
    analysis = script_check.analyse(txt_path, args)
    assert analysis["flags"] == []

    json_path = tmp_path / "plan.json"
    export_script_json(reparsed, json_path)
    loaded = load_script_json(json_path)
    assert loaded.title == reparsed.title
    assert len(loaded.scenes) == len(reparsed.scenes)
