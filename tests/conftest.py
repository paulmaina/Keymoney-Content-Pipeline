import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def no_user_defaults(monkeypatch, tmp_path, tmp_path_factory):
    """Tests never read the real make_video.toml, .env or VIDEO_STYLE."""
    import ai_visuals
    import make_video
    monkeypatch.setattr(make_video, "CONFIG_FILE", tmp_path / "no-config.toml")
    monkeypatch.setattr(ai_visuals, "ENV_FILE", tmp_path / "no.env")
    # nor count fake Cloudflare usage against today's real allowance
    monkeypatch.setattr(ai_visuals, "usage_path", lambda: tmp_path / "cloudflare_usage.json")
    monkeypatch.delenv("VIDEO_STYLE", raising=False)
    monkeypatch.delenv("KAGGLE_API_TOKEN", raising=False)  # upscaling runs on every render: never reach real Kaggle
    # nor the real channel profiles and branding clips: a plain default channel with the built-in option defaults
    import channels
    folder = tmp_path_factory.mktemp("channels")
    (folder / f"{channels.DEFAULT}.toml").write_text('name = "Smart Village Boy"\n', encoding="utf-8")
    monkeypatch.setattr(channels, "CHANNELS_DIR", folder)
    # unit tests never render, so never install Remotion's packages (the smoke test uses the real thing)
    import remotion_render
    monkeypatch.setattr(remotion_render, "ensure_installed", lambda: None)
