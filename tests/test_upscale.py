"""Tests for --upscale: batching, the cache and falling back to the originals. Kaggle is faked."""
import json

import pytest
from PIL import Image

import make_video as mv
import upscale
from test_kaggle_runner import FakeCLI


@pytest.fixture(autouse=True)
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(upscale, "UPSCALED_DIR", tmp_path / "upscaled")
    monkeypatch.setattr(upscale, "WORK", tmp_path / "upscaled" / "_kaggle")
    return tmp_path / "upscaled"


def picture(path, size, colour=(90, 140, 60)):
    Image.new("RGB", size, colour).save(path)
    return path


def notebook(scale=2):
    """What kaggle/upscale.ipynb leaves in /kaggle/working for the job it was given."""
    def output(dest):
        job = json.loads((upscale.WORK / "job" / "job.json").read_text(encoding="utf-8"))
        done = []
        for item in job["images"]:
            with Image.open(upscale.WORK / "job" / item["file"]) as img:
                big = img.resize((img.width * scale, img.height * scale))
            big.save(dest / f"{item['key']}.jpg")
            done.append({"key": item["key"], "scale": scale, "width": big.width, "height": big.height, "seconds": 2.0})
        (dest / "manifest.json").write_text(json.dumps({"images": done}), encoding="utf-8")
    return output


def run(scenes, cli, formats=("horizontal", "vertical")):
    return upscale.upscale_scenes(scenes, list(formats), cli=cli, sleep=lambda s: None,
                                  clock=iter(range(0, 10**6, 60)).__next__)


def test_all_small_images_go_in_one_batch_and_are_used(tmp_path, capsys):
    wide, tall = picture(tmp_path / "w.jpg", (1536, 864)), picture(tmp_path / "t.jpg", (864, 1536), (10, 20, 30))
    big = picture(tmp_path / "big.jpg", (2400, 1350))
    scenes = [{"src": wide, "srcs": {"horizontal": wide, "vertical": tall}, "visual": None},
              {"src": big, "visual": "big.jpg"}, {"src": None, "visual": None}]
    cli = FakeCLI(output=notebook())
    used = run(scenes, cli)
    assert len(cli.did("kernels", "push")) == 1  # one Kaggle run for every image
    assert sorted(cli.uploaded[0]) == sorted(["dataset-metadata.json", "job.json",
                                              f"{upscale.key(wide)}.jpg", f"{upscale.key(tall)}.jpg"])
    s = scenes[0]
    assert s["srcs"]["horizontal"].parent == upscale.UPSCALED_DIR and s["src"] == s["srcs"]["horizontal"]
    with Image.open(s["srcs"]["vertical"]) as img:
        assert img.size == (1728, 3072)
    assert s["upscaled_from"] == wide and scenes[1]["src"] == big  # already 2048+ px: left alone
    out = capsys.readouterr().out
    assert used == pytest.approx(251.4 / 60) and "GPU time used: about 4.2 min" in out
    assert "upscaled 2 of 2 image(s)" in out and "2.0 s per image on the GPU" in out


def test_each_image_is_upscaled_only_once(tmp_path):
    wide = picture(tmp_path / "w.jpg", (1536, 864))
    run([{"src": wide, "visual": None}], FakeCLI(output=notebook()))
    cli = FakeCLI(output=notebook())
    scenes = [{"src": wide, "visual": None}]
    assert run(scenes, cli) is None and cli.calls == []  # cached: Kaggle isn't touched
    assert scenes[0]["src"].parent == upscale.UPSCALED_DIR


def test_a_failed_run_keeps_the_originals(tmp_path, capsys):
    wide = picture(tmp_path / "w.jpg", (1536, 864))
    scenes = [{"src": wide, "visual": None}]
    run(scenes, FakeCLI(statuses=["RUNNING", "ERROR"]))
    assert scenes[0]["src"] == wide
    assert "0 of 1 image(s)" in capsys.readouterr().out


def test_too_small_results_are_not_cached(tmp_path):
    wide = picture(tmp_path / "w.jpg", (800, 450))
    scenes = [{"src": wide, "visual": None}]
    run(scenes, FakeCLI(output=notebook(scale=2)))  # 1600 px: still under 2048
    assert scenes[0]["src"] == wide and not upscale.cached_path(wide).exists()


def test_videos_and_cards_are_not_sent(tmp_path):
    clip = tmp_path / "mine.mp4"
    clip.write_bytes(b"video")
    scenes = [{"src": clip, "visual": "mine.mp4"}, {"src": None, "visual": None}]
    cli = FakeCLI()
    run(scenes, cli)
    assert cli.calls == []


def test_upscaled_images_reach_the_renderers(tmp_path):
    wide, tall = picture(tmp_path / "w.jpg", (1536, 864)), picture(tmp_path / "t.jpg", (864, 1536), (9, 9, 9))
    scenes = [{"src": wide, "srcs": {"horizontal": wide, "vertical": tall}, "visual": None}]
    run(scenes, FakeCLI(output=notebook()))
    assert mv.scene_src(scenes[0], "vertical") == upscale.cached_path(tall)
    assert mv.scene_src(scenes[0], "horizontal") == upscale.cached_path(wide)


def test_time_cap_grows_with_the_batch():
    assert upscale.hours_cap(1) == pytest.approx(0.26)
    assert upscale.hours_cap(1000) == upscale.MAX_HOURS


def test_cli_flags():
    a = mv.parse_args(["s.txt", "--upscale"])
    assert a.upscale
    for gone in ("--kaggle-video", "--kaggle-resume", "--video-scenes", "--export-video-job", "--clips", "--clip-fill"):
        with pytest.raises(SystemExit):
            mv.parse_args(["s.txt", gone, "1"])
