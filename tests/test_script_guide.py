"""SCRIPT_GUIDE.md's example (Scripts/TEMPLATE.txt) must keep passing --check as the tool changes. No network."""
import re
from pathlib import Path

import make_video as mv
import script_check
import shorts
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
    minutes = a["video_seconds"] / 60
    assert 8 <= minutes <= 10.5  # an 8-10 minute main video
    assert 6 <= len(rows) / minutes <= 8  # 6-8 scenes a minute
    assert all(r["description"] for r in rows)
    lo, hi = script_check.video_range(len(rows))
    assert lo <= sum(r["video"] for r in rows) <= hi
    assert mv.parse_characters(TEMPLATE) == {"Smart Village Boy": HOST.split(" = ", 1)[1]}


def test_template_s_shorts_cut_only_at_chapter_headings(tmp_path):
    a = check(tmp_path)
    parts, seconds = a["parts"], [r["seconds"] for r in a["rows"]]
    starts = shorts.chapter_starts(mv.parse_sections(TEMPLATE))
    assert len(parts) >= 3 and all(x in starts for x, _ in parts[1:])
    assert all(shorts.part_seconds(seconds[x:y]) <= shorts.LIMIT for x, y in parts)


def test_template_s_cloudflare_days_match_the_guide(tmp_path):
    one = check(tmp_path, "--check-images", "--no-two-shots")
    est = one["estimates"]
    assert est["days"] == -(-int(est["neurons_total"]) // 10_000) and est["days"] == 3
    per_scene = (est["neurons_total"] - est["neurons_meta"]) / len(one["rows"])
    assert 360 <= per_scene <= 420  # the guide's ~390 neurons a scene with one shot
    two = check(tmp_path, "--check-images", "--two-shots")
    est2 = two["estimates"]
    per_scene2 = (est2["neurons_total"] - est2["neurons_meta"]) / len(two["rows"])
    assert 720 <= per_scene2 <= 820 and est2["days"] == 5  # ~770 with second shots (every template scene is over 6 s)


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
