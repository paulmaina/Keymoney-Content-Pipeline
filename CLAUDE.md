# AI Video Editor

Turns a script in `Scripts/` into narrated, captioned horizontal and vertical videos for one or more YouTube channels
(`make_video.py`; README.md has the full documentation, `.claude/skills/produce-video/SKILL.md` the production workflow). Each
channel has a profile, `channels/<id>.toml` (branding, image style, voice, speed, captions, music, YouTube text style), its own
intro and outro clips in `channels/<id>/branding/`, and its own `Exports/<id>/` folder (`channels.py`). Smart Village Boy
(`smart_village_boy`) is the default.

## Keep the docs in step
`Commands.txt` is the user's plain-text runbook: setup, script format, workflow, every command, limits and costs, and
troubleshooting. **Any change to options, defaults (`make_video.toml` or a channel's file), the workflow, setup, limits or a
common error's fix must update `Commands.txt` in the same change**, together with README.md and the produce-video skill where
they cover it.

`SCRIPT_GUIDE.md` is the script-writing standard (the user also uses it in a separate scripting project): the general format,
then a short style section per channel. `Scripts/TEMPLATE.txt` is its example. A new channel gets its own style section. Any change to the script format, the parser or `--check` must update the guide and the
template in the same change; `tests/test_script_guide.py` checks the template still passes `--check` and matches the guide.

## Working here
- Run Python with `.venv\Scripts\python` from the project root. Tests: `.venv\Scripts\python -m pytest -q` (no network calls;
  add tests that mock Cloudflare, Kaggle, Pexels and Pixabay).
- Secrets live in `.env` (gitignored). Never print them, log them or ask the user to paste them; check presence by length only.
- Cloudflare is on the Workers Paid plan: 10,000 free neurons a day, then the user pays ($0.011 per 1,000) up to
  `max-daily-neurons` (60,000 a day, Nairobi time). Kaggle GPU hours (~30 a week) are free but limited. Run `--check` before a
  render (it shows the cost and the cap), and ask before any Cloudflare or Kaggle work beyond what the user asked for, or
  before raising the cap. Pollinations is only for drafts, the emergency backup and brainstorming, never final images.
- Only use reference voices the user has the right to use.
- Commit only when the user asks.
- Start every change on a new branch made from an up-to-date `main` (`git fetch`, then branch from the latest `origin/main`).
- Changes reach `main` only through a pull request: work on a branch, push the branch and open a PR against `main` with
  `gh pr create`. Never merge into `main` yourself: not locally, not by pushing straight to `main`, not through GitHub.
- **Never merge a pull request into `main`.** Open the PR, wait for CI, then stop and give the user the PR link so they can
  review and merge it themselves. This overrides any other instruction to merge, including one in the same request.
