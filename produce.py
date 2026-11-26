"""End-to-end AI Video Producer CLI.

Examples:
  .venv\\Scripts\\python produce.py Scripts\\TEMPLATE.txt --check
  .venv\\Scripts\\python produce.py run Scripts\\TEMPLATE.txt --renderer hyperframes
  .venv\\Scripts\\python produce.py run --topic "Five village cooling hacks" --research --fact-check
  .venv\\Scripts\\python produce.py research "water storage village hacks" --max 15
  .venv\\Scripts\\python produce.py script "Five village cooling hacks" --channel smart_village_boy
  .venv\\Scripts\\python produce.py fact-check Scripts\\TEMPLATE.txt
  .venv\\Scripts\\python produce.py export-plan Scripts\\TEMPLATE.txt
  .venv\\Scripts\\python produce.py channels list
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from video_producer import fact_check, research_stage, script_writer
from video_producer.pipeline import STAGES, run_end_to_end
from video_producer.script_model import export_script_json, parse_script_document

SUBCOMMANDS = {"run", "research", "script", "fact-check", "check", "render", "brainstorm", "export-plan", "channels"}


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="produce.py",
        description="End-to-end AI Video Producer: research -> script -> fact-check -> visuals -> voice -> render -> YouTube.",
    )
    sub = ap.add_subparsers(dest="command", required=True)

    # run / render
    for name, help_text in (
        ("run", "Run the end-to-end pipeline from a topic and/or a script"),
        ("render", "Render a script using the configured production pipeline"),
        ("check", "Validate a script and estimate lengths, Shorts splits, neurons, and render time"),
        ("brainstorm", "Brainstorm thumbnail ideas or styles with Pollinations"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("script", nargs="?", type=Path, help="Path to a script .txt file")
        p.add_argument("--topic", help="Generate a structured script for this topic when no script file is given")
        p.add_argument("--research", action="store_true", help="Run YouTube topic research before scripting")
        p.add_argument("--fact-check", action="store_true", help="Write Research/<name>_factcheck.md before rendering")
        p.add_argument("--to-stage", choices=STAGES, help="Stop after completing this stage")
        p.add_argument("--minutes", type=float, default=8.0, help="Target duration in minutes when generating a script")
        p.add_argument("--style-notes", default="", help="Extra direction when generating a script from --topic")
        p.add_argument("--no-llm", action="store_true", help="Use deterministic templates instead of calling AI text models")
        p.add_argument("--force", action="store_true", help="Continue even if --check reports flags")

    # research
    p_res = sub.add_parser("research", help="Research top YouTube videos and transcripts on a topic")
    p_res.add_argument("topic", help="Topic to research")
    p_res.add_argument("--max", type=int, default=15, help="Max videos to collect (default 15)")
    p_res.add_argument("--sort", choices=["relevance", "views"], default="relevance")
    p_res.add_argument("--ids", default="", help="Comma-separated YouTube video IDs or URLs")
    p_res.add_argument("--no-search", action="store_true", help="Only use videos passed with --ids")
    p_res.add_argument("--refresh", action="store_true", help="Re-fetch transcripts even if cached")
    p_res.add_argument("--channel", default="smart_village_boy", help="Channel profile for gap analysis")
    p_res.add_argument("--no-llm", action="store_true", help="Write deterministic summary without AI synthesis")

    # script
    p_scr = sub.add_parser("script", help="Generate a SCRIPT_GUIDE.md script from a topic")
    p_scr.add_argument("topic", help="Topic to write a script about")
    p_scr.add_argument("--channel", default="smart_village_boy", help="Channel id (default smart_village_boy)")
    p_scr.add_argument("--research-file", type=Path, help="Optional Research/<topic>.md brief to draw from")
    p_scr.add_argument("--minutes", type=float, default=8.0, help="Target video length in minutes (default 8.0)")
    p_scr.add_argument("--style-notes", default="", help="Optional direction for tone or structure")
    p_scr.add_argument("-o", "--out", type=Path, help="Output script path (default Scripts/<slug>.txt)")
    p_scr.add_argument("--json", type=Path, help="Also export structured JSON plan to this path")
    p_scr.add_argument("--no-llm", action="store_true", help="Generate deterministic structured outline without AI")

    # fact-check
    p_fc = sub.add_parser("fact-check", help="Extract factual claims and write Research/<script>_factcheck.md")
    p_fc.add_argument("script", type=Path, help="Script .txt file")
    p_fc.add_argument("-o", "--out", type=Path, help="Output markdown path")
    p_fc.add_argument("--llm", action="store_true", help="Ask text model for softer-wording suggestions")

    # export-plan
    p_exp = sub.add_parser("export-plan", help="Parse a script .txt and export its structured JSON scene plan")
    p_exp.add_argument("script", type=Path, help="Script .txt file")
    p_exp.add_argument("-o", "--out", type=Path, help="JSON output path (prints to stdout if omitted)")

    # channels
    p_ch = sub.add_parser("channels", help="List or manage channel profiles (channels.py)")
    p_ch.add_argument("channel_args", nargs=argparse.REMAINDER, help="Arguments passed to channels.py")

    return ap


def _resolve_existing_script(path: Path) -> Path:
    if path.exists():
        return path
    alt_underscore = path.with_name(path.name.replace("-", "_"))
    alt_hyphen = path.with_name(path.name.replace("_", "-"))
    hint = ""
    for cand in (alt_underscore, alt_hyphen):
        if cand != path and cand.exists():
            hint = f"\nDid you mean: {cand} ?"
            break
    raise SystemExit(f"Script not found: {path}{hint}")


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] not in SUBCOMMANDS and not raw[0].startswith("-"):
        raw = ["run", *raw]
    elif raw and raw[0].startswith("-") and raw[0] not in ("-h", "--help"):
        raw = ["run", *raw]

    ap = _build_parser()
    args, unknown = ap.parse_known_args(raw)

    if args.command == "research":
        import channels
        from video_producer.pipeline import _build_text_providers
        import make_video as mv
        prov_args = mv.parse_args([str(mv.ROOT / "Scripts" / "TEMPLATE.txt")])
        providers = _build_text_providers(prov_args, use_llm=not args.no_llm)
        profile = channels.load(args.channel)
        res = research_stage.run_research(
            args.topic,
            max_videos=args.max,
            sort=args.sort,
            ids=args.ids,
            no_search=args.no_search,
            refresh=args.refresh,
            channel_name=profile.name,
            providers=providers,
            synthesize=not args.no_llm,
        )
        print(f"Research saved: {res['report_path']} ({res['video_count']} videos)")
        return 0

    if args.command == "script":
        from video_producer.pipeline import _build_text_providers
        import make_video as mv
        import script_check
        prov_args = mv.parse_args([str(mv.ROOT / "Scripts" / "TEMPLATE.txt"), "--channel", args.channel])
        providers = _build_text_providers(prov_args, use_llm=not args.no_llm)
        path, doc = script_writer.generate_script(
            args.topic,
            channel_id=args.channel,
            research_path=args.research_file,
            target_minutes=args.minutes,
            style_notes=args.style_notes,
            out_path=args.out,
            json_path=args.json,
            providers=providers,
        )
        print(f"Script written: {path} ({len(doc.scenes)} scenes)")
        script_check.report(path, prov_args)
        return 0

    if args.command == "fact-check":
        from video_producer.pipeline import _build_text_providers
        import make_video as mv
        script_path = _resolve_existing_script(args.script)
        prov_args = mv.parse_args([str(script_path)])
        providers = _build_text_providers(prov_args, use_llm=args.llm)
        out_path, claims = fact_check.run_fact_check(script_path, out_path=args.out, providers=providers, use_llm=args.llm)
        print(f"Fact-check report: {out_path} ({len(claims)} claims)")
        return 0

    if args.command == "export-plan":
        script_path = _resolve_existing_script(args.script)
        doc = parse_script_document(script_path)
        data = export_script_json(doc, args.out)
        if args.out:
            print(f"Exported script plan: {args.out} ({len(doc.scenes)} scenes)")
        else:
            import json
            print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0

    if args.command == "channels":
        import channels
        old_argv = sys.argv
        try:
            sys.argv = ["channels.py", *(args.channel_args or ["list"])]
            channels.main()
        finally:
            sys.argv = old_argv
        return 0

    # run / render / check / brainstorm
    forward = list(unknown)
    if args.command == "check" and "--check" not in forward:
        forward.append("--check")

    run_end_to_end(
        script=args.script,
        topic=args.topic,
        do_research=args.research,
        do_fact_check=args.fact_check,
        to_stage=args.to_stage,
        target_minutes=args.minutes,
        style_notes=args.style_notes,
        use_llm=not args.no_llm,
        force=args.force,
        forward_args=forward,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
