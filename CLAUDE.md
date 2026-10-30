# AI Video Editor

Turns a script in `Scripts/` into narrated, captioned horizontal and vertical videos for the Smart Village Boy channel
(`make_video.py`; README.md has the full documentation, `.claude/skills/produce-video/SKILL.md` the production workflow).

## Keep the docs in step
`Commands.txt` is the user's plain-text runbook: setup, script format, workflow, every command, limits and costs, and
troubleshooting. **Any change to options, defaults (`make_video.toml`), the workflow, setup, limits or a common error's fix must
update `Commands.txt` in the same change**, together with README.md and the produce-video skill where they cover it.

`SCRIPT_GUIDE.md` is the script-writing standard (the user also uses it in a separate scripting project), with
`Scripts/TEMPLATE.txt` as its example. Any change to the script format, the parser or `--check` must update the guide and the
template in the same change; `tests/test_script_guide.py` checks the template still passes `--check` and matches the guide.

## Working here
- Run Python with `.venv\Scripts\python` from the project root. Tests: `.venv\Scripts\python -m pytest -q` (no network calls;
  add tests that mock Cloudflare, Kaggle, Pexels and Pixabay).
- Secrets live in `.env` (gitignored). Never print them, log them or ask the user to paste them; check presence by length only.
- Cloudflare neurons (10,000 a day) and Kaggle GPU hours (~30 a week) are the user's free allowance: run `--check` before a
  render, and ask before any Cloudflare or Kaggle work beyond what the user asked for.
- Only use reference voices the user has the right to use.
- Commit only when the user asks.
