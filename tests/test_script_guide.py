"""SCRIPT_GUIDE.md's example (Scripts/TEMPLATE.txt) must keep passing --check as the tool changes. No network."""
import re
from pathlib import Path

import make_video as mv
import script_check
import youtube_meta

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "Scripts" / "TEMPLATE.txt"
GUIDE = ROOT / "SCRIPT_GUIDE.md"
HOST = ("# character: Smart Village Boy = Kenyan boy, twelve years old, dark skin, red cap, yellow T-shirt, "
        "short black hair, bright smile")


def check(tmp_path, *options):
    args = mv.parse_args([str(TEMPLATE), "-o", str(tmp_path / "template"), "--generate-visuals", *options])
    return script_check.analyse(TEMPLATE, args)


def test_template_passes_check_with_no_flags_or_notes(tmp_path):
    a = check(tmp_path)
    assert a["flags"] == [] and a["notes"] == []
    assert [n for n, _ in a["comments"]] == [1, 2]  # the title and the note; character, media, location lines are used


def test_template_follows_the_guide_s_standard(tmp_path):
    a = check(tmp_path, "--engine", "chatterbox", "--rate", "+5%")
    rows = a["rows"]
    assert 15 <= len(rows) <= 17 and 400 <= a["words"] <= 430
    assert a["total_seconds"] <= 170  # 2:50, so the vertical video stays a Short with the intro and outro
    assert all(r["description"] for r in rows) and 4 <= sum(r["video"] for r in rows) <= 5
    assert a["estimates"]["neurons_total"] < 10_000
    assert mv.parse_characters(TEMPLATE) == {"Smart Village Boy": HOST.split(" = ", 1)[1]}


def test_template_chapters_title_and_places():
    sections = mv.parse_sections(TEMPLATE)
    assert sections[0] == "HOOK" and sections[-1] == "OUTRO"
    for name in set(sections):
        assert sections.count(name) >= 2, f"section {name} needs at least 2 scenes for a 10-second chapter"
    assert youtube_meta.script_title(TEMPLATE) == "Five Village Water Hacks That Beat Drought"
    import places
    labels = [p.label for _, p, _, _ in places.scene_places(mv.parse_script(TEMPLATE), TEMPLATE)]
    assert labels == ["Kitui, Kenya", "Burkina Faso", "Iran", "Meghalaya, India"]


def test_guide_example_is_the_template_word_for_word():
    guide = GUIDE.read_text(encoding="utf-8")
    example = re.search(r"## 6\. Example script.*?```text\n(.*?)\n```", guide, re.S).group(1)
    assert example == TEMPLATE.read_text(encoding="utf-8").rstrip("\n"), "update SCRIPT_GUIDE.md section 6"
    assert HOST in guide and HOST in example
