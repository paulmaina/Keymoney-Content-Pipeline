"""End-to-end production orchestrator tying together research, script authoring,
fact-checking, preflight check, visuals, voiceover, captions, rendering, Shorts,
and YouTube metadata.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import ai_visuals
import channels
import make_video as mv
import outputs
import script_check
from video_producer import fact_check, research_stage, script_writer
from video_producer.script_model import export_script_json, parse_script_document

STAGES = (
    "research",
    "script",
    "fact-check",
    "check",
    "visuals",
    "voice",
    "captions",
    "render",
    "metadata",
)


@dataclass
class PipelineReport:
    script_path: Path | None = None
    channel_id: str = "smart_village_boy"
    renderer: str = "remotion"
    topic: str | None = None
    research_report: Path | None = None
    fact_check_report: Path | None = None
    script_plan_path: Path | None = None
    upload_dir: Path | None = None
    working_dir: Path | None = None
    stages_completed: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("script_path", "research_report", "fact_check_report", "script_plan_path", "upload_dir", "working_dir"):
            if d[k] is not None:
                d[k] = str(d[k])
        return d


def _build_text_providers(args, use_llm: bool):
    if not use_llm:
        return None
    ai_visuals.load_env()
    creds = ai_visuals.credentials(required=False)
    poll_key = ai_visuals.pollinations_key()
    if not creds[0] and not poll_key:
        return None
    mode = "draft" if (getattr(args, "draft", False) or not creds[0]) else "final"
    if mode == "draft" and not poll_key:
        return None
    return ai_visuals.Providers(
        mode=mode,
        image_model=args.image_model,
        text_model=args.text_model,
        creds=creds,
        poll_key=poll_key,
        cap=args.max_daily_neurons,
    )


def run_end_to_end(
    script: Path | None = None,
    *,
    topic: str | None = None,
    do_research: bool = False,
    do_fact_check: bool = False,
    to_stage: str | None = None,
    target_minutes: float = 8.0,
    style_notes: str = "",
    use_llm: bool = True,
    force: bool = False,
    forward_args: list[str] | None = None,
) -> PipelineReport:
    """Run the end-to-end video production pipeline from a topic and/or a script file."""
    if not script and not topic:
        raise SystemExit("Provide either a script path or --topic \"...\".")

    # Resolve defaults from make_video.toml + channel profile
    probe_script = script or (mv.ROOT / "Scripts" / "TEMPLATE.txt")
    base_argv = [str(probe_script), *(forward_args or [])]
    args = mv.parse_args(base_argv)
    profile = getattr(args, "profile", None) or channels.load(args.channel_id)
    report = PipelineReport(channel_id=profile.id, renderer=args.renderer, topic=topic)

    providers = _build_text_providers(args, use_llm=use_llm)

    if topic and do_research:
        res = research_stage.run_research(
            topic,
            channel_name=profile.name,
            providers=providers,
            synthesize=use_llm,
        )
        report.research_report = res["report_path"]
        report.stages_completed.append("research")
        print(f"Research brief: {report.research_report}")
        if to_stage == "research":
            return report

    if script is None:
        assert topic is not None
        target_script, _ = script_writer.generate_script(
            topic,
            channel_id=profile.id,
            research_path=report.research_report,
            target_minutes=target_minutes,
            style_notes=style_notes,
            providers=providers,
        )
        script = target_script
        report.stages_completed.append("script")
        print(f"Generated script: {script}")
    else:
        script = Path(script).resolve()
        if not script.exists():
            raise SystemExit(f"Script not found: {script}")

    report.script_path = script

    # Re-parse CLI args with the real script so `# channel:` inside the script wins
    args = mv.parse_args([str(script), *(forward_args or [])])
    report.channel_id = args.channel_id
    report.renderer = args.renderer

    if to_stage == "script":
        return report

    if do_fact_check or to_stage == "fact-check":
        fc_path, claims = fact_check.run_fact_check(script, providers=providers, use_llm=use_llm)
        report.fact_check_report = fc_path
        report.stages_completed.append("fact-check")
        print(f"Fact-check report ({len(claims)} claims): {fc_path}")
        if to_stage == "fact-check":
            return report

    analysis = script_check.analyse(script, args)
    report.flags = list(analysis["flags"])
    report.stages_completed.append("check")

    if args.check or to_stage == "check":
        script_check.report(script, args)
        return report

    if report.flags and not force:
        script_check.report(script, args)
        raise SystemExit(
            f"Script check found {len(report.flags)} flag(s). Fix them or rerun with --force to render anyway."
        )

    out = outputs.Outputs.for_script(args.output, script, args.channel_id).make()
    report.upload_dir = out.upload
    report.working_dir = out.working

    doc = parse_script_document(script, default_channel=args.channel_id)
    plan_path = out.working / "script_plan.json"
    export_script_json(doc, plan_path)
    report.script_plan_path = plan_path

    if providers is not None:
        providers.save_usage()

    # Delegate downstream media, audio, render, Shorts, and metadata stages to make_video.main()
    # via produce() so all checkpoints, flags, and test monkeypatches behave identically.
    mv.check_ffmpeg()
    font_path = args.font.resolve()
    if not font_path.exists():
        raise SystemExit(f"Font not found: {font_path}")
    if args.music:
        args.music = args.music.resolve()
        if not args.music.exists():
            raise SystemExit(f"Music file not found: {args.music}")
    args.renderer = mv.resolve_renderer(args.renderer)

    scenes = mv.parse_script(script)
    if not scenes:
        raise SystemExit("Script has no text.")
    mv.assign_visuals(scenes, script.parent, use_pool=not args.generate_visuals)

    with outputs.Tee(out.log):
        if args.thumbnail_ideas or args.style_test:
            import brainstorm
            brainstorm.run(args, script, scenes, font_path, out)
            report.stages_completed.append("brainstorm")
        else:
            mv.produce(args, script, scenes, font_path, out)
            if args.prompts_only:
                report.stages_completed.append("prompts")
            elif args.visuals_only:
                report.stages_completed.append("visuals")
            else:
                for stg in ("visuals", "voice", "captions", "render"):
                    report.stages_completed.append(stg)
                if not args.no_metadata and not args.draft:
                    report.stages_completed.append("metadata")

    state_path = out.working / "pipeline_state.json"
    state_path.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return report
