"""Tests for the caption styles: key words, line cuts, and keeping lines off faces and labels. No video is rendered."""
import json

import pytest
from PIL import Image

import captions as cap
import make_video as mv
from make_video import Word


def keyed(text, names=(), places=()):
    toks = text.split()
    return [toks[i] for i in sorted(cap.key_words(text, names, places))]


def test_key_words_are_numbers_places_names_and_hack_names():
    assert keyed("In northern Nigeria, the heat can spoil vegetables in just a few days.",
                 places=["Northern Nigeria"]) == ["Nigeria,"]
    assert keyed("A teacher named Mohammed Bah Abba popularised an old idea.") == ["Mohammed", "Bah", "Abba"]
    assert keyed("People call it the zeer, or the pot-in-pot cooler.") == ["zeer,"]
    assert keyed("A movement called Liter of Light carried his idea to the Philippines.") == \
        ["Liter", "of", "Light", "Philippines."]
    assert keyed("It froze into a towering cone of ice called an ice stupa.") == ["ice", "stupa."]
    assert keyed("Hundreds of thousands of homes, 40 to 60 watts.") == ["Hundreds", "thousands", "40", "60"]
    assert keyed("Five villages. Not one needed a big budget.") == ["Five", "one"]  # sentence starts aren't names


def test_declared_names_count_even_at_a_sentence_start():
    assert keyed("Sonam piped meltwater downhill.", names=["Sonam Wangchuk"]) == ["Sonam"]


def words(text, t0=0.0, step=0.4):
    return [Word(w, t0 + i * step, t0 + i * step + 0.3) for i, w in enumerate(text.split())]


def test_mark_keys_follows_whisper_words_back_to_the_script():
    scenes = [{"text": "In Brazil, a mechanic named Alfredo Moser was tired."}]
    chunk = words("In Brazil, a mechanic named Alfredo Moser was tired.")
    chunk[5:7] = [Word("Alfredo Moser", 2.0, 2.6)]  # captions can merge words
    flags = cap.mark_keys([chunk], scenes)
    assert [w.text for w, k in zip(chunk, flags) if k] == ["Brazil,", "Alfredo Moser"]


def test_bold_lines_are_2_to_4_words_and_clean_lines_longer():
    scenes = [{"text": "Over many years the roots thicken and weave into a bridge that can carry dozens of people"}]
    chunks = [words(scenes[0]["text"])]
    bold = cap.layout(chunks, scenes, "vertical", "bold", 30, 0, [0])
    assert all(1 <= len(l["words"]) <= 4 for l in bold) and sum(len(l["words"]) for l in bold) == 17
    assert max(len(l["words"]) for l in bold) >= 3
    clean = cap.layout(chunks, scenes, "horizontal", "clean", 30, 0, [0])
    assert len(clean) < len(bold) and all(len(l["words"]) <= 8 for l in clean)
    # word timings stay on the voice: frame of each word = lead + its start
    lead = 52
    lines = cap.layout(chunks, scenes, "vertical", "bold", 30, lead, [lead])
    assert lines[0]["words"][1]["start"] == lead + round(0.4 * 30) and lines[0]["start"] == lead
    assert any(w["key"] for l in bold for w in l["words"] if w["text"] == "dozens")


def test_vertical_bold_sits_in_the_lower_third_clear_of_shorts_and_labels():
    for y in cap.POSITIONS["vertical"]["bold"]:
        assert 0.5 < y - cap.LINE_HEIGHT["bold"] / 2 and y + cap.LINE_HEIGHT["bold"] / 2 < 0.78  # Shorts' title below 78%
    assert all(y > 0.5 for y in cap.POSITIONS["vertical"]["clean"])  # map and label end by ~45%


def test_lines_move_off_faces():
    assert cap.place_line(1, "vertical", "bold", []) == 0.64
    assert cap.place_line(1, "vertical", "bold", [(0.55, 0.67)]) == 0.71  # 0.64 and 0.57 would cover the face
    assert cap.place_line(1, "horizontal", "clean", [(0.80, 0.95)]) == 0.75
    # nowhere is clear: the least-covered spot wins
    assert cap.place_line(1, "vertical", "bold", [(0.5, 0.9)]) in cap.POSITIONS["vertical"]["bold"]


def test_each_line_uses_the_faces_of_its_own_scene():
    scenes = [{"text": "One two three."}, {"text": "Four five six."}]
    chunks = [words("One two three.", 0.0), words("Four five six.", 5.0)]
    lines = cap.layout(chunks, scenes, "vertical", "bold", 30, 0, [0, 150], face_boxes={1: [(0.55, 0.67)]})
    assert lines[0]["y"] == 0.64 and lines[-1]["y"] == 0.71


def test_faces_map_onto_the_cover_cropped_frame(tmp_path, monkeypatch):
    img = tmp_path / "wide.jpg"
    Image.new("RGB", (1536, 864)).save(img)
    # a face near the left edge is cropped out of the 9:16 frame; one in the middle is kept
    (tmp_path / f"wide.{cap.FACES_VERSION}.faces.json").write_text(json.dumps([[0.02, 0.3, 0.05, 0.1], [0.45, 0.4, 0.1, 0.2]]))
    v = cap.frame_faces(img, "vertical")
    assert len(v) == 1 and v[0] == pytest.approx((0.4 - cap.FACE_MARGIN, 0.6 + cap.FACE_MARGIN))
    assert len(cap.frame_faces(img, "horizontal")) == 2


def test_face_results_are_cached_and_dropped_with_a_redrawn_image(tmp_path, monkeypatch):
    import ai_visuals as av
    monkeypatch.setattr(av, "GEN_DIR", tmp_path)
    img = av.save_image(_jpeg(), tmp_path / "k.jpg")
    calls = []
    monkeypatch.setattr(cap, "detect_faces", lambda im: calls.append(1) or [(10, 10, 20, 20)])
    assert len(cap.faces(img)) == 1 and len(cap.faces(img)) == 1 and calls == [1]
    av.save_image(_jpeg(), img)  # a redraw removes <image>.*.faces.json
    cap.faces(img)
    assert calls == [1, 1]


def _jpeg():
    import io
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (100, 120, 140)).save(buf, "JPEG")
    return buf.getvalue()


def test_no_opencv_means_no_faces(tmp_path, monkeypatch):
    import builtins
    real = builtins.__import__
    monkeypatch.setattr(builtins, "__import__", lambda name, *a, **k: (_ for _ in ()).throw(ImportError())
                        if name == "cv2" else real(name, *a, **k))
    img = tmp_path / "x.jpg"
    img.write_bytes(_jpeg())
    assert cap.faces(img) == []


def test_style_option_and_defaults():
    assert cap.style_for("vertical", "auto") == "bold" and cap.style_for("horizontal", "auto") == "clean"
    assert cap.style_for("vertical", "minimal") == "minimal"
    assert mv.parse_args(["s.txt"]).caption_style == "auto"
    assert mv.parse_args(["s.txt", "--caption-style", "clean"]).caption_style == "clean"
    with pytest.raises(SystemExit):
        mv.parse_args(["s.txt", "--caption-style", "loud"])


def test_props_carry_the_style_per_format():
    from types import SimpleNamespace
    import remotion_render as rr
    lead, lengths, bounds, total = rr.timeline([5], 30, 0, 0)
    a = SimpleNamespace(no_branding=True, channel="C", caption_style="auto")
    p = rr.build_props("vertical", [{"text": "A"}], [{"kind": "card"}], ["zoom-in"], lengths, bounds, total, [], [], a)
    assert p["captionStyle"] == "bold"
    assert rr.build_props("horizontal", [{"text": "A"}], [{"kind": "card"}], ["zoom-in"], lengths, bounds, total, [],
                          [], a)["captionStyle"] == "clean"
