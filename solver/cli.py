"""Unified command line: ``python -m solver <plan|next|mark|status>``.

The slash commands call these four verbs through ``bin/speckit-schedule``;
everything else in the package stays reachable as ``python -m solver.<module>``
for advanced pipelines.

* ``plan``   — tasks.md → schedule.md + schedule.json (+ inline summary).
* ``next``   — the next execution round with one brief per lane, derived
  from the plan and the ``[x]`` state of tasks.md.
* ``mark``   — flip ``- [ ] T###`` checkboxes (the orchestrator is the only
  writer of tasks.md).
* ``status`` — installation self-diagnosis (delegates to ``solver.status``).
"""

from __future__ import annotations

__all__ = ["main"]

import argparse
import hashlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped, unused-ignore]  # PyYAML ships no type stubs by default

from ._paths import project_root
from .briefs import BriefContext, format_blocked, parse_report, render_brief, render_round
from .config_schema import resolve_config_path
from .defaults import STATUS_FEASIBLE, STATUS_OPTIMAL
from .i18n import t
from .parse_tasks import parse_tasks_md
from .render_schedule import render as render_markdown
from .result.summary import format_inline_summary
from .rounds import lane_queues, next_round, predecessor_map, round_to_dict
from .scheduler import solve_from_json
from .tasks_md import mark_tasks, scan_checkboxes
from .validation import ScheduleInputError
from .warnings_collector import WarningCollector

log = logging.getLogger(__name__)

SCHEDULE_MD = "schedule.md"
SCHEDULE_JSON = "schedule.json"

EXIT_OK = 0
EXIT_NOT_SOLVED = 1
EXIT_INPUT_ERROR = 2
EXIT_BLOCKED = 3


# ───────────────────────────────────────────────────────────────────────
# helpers
# ───────────────────────────────────────────────────────────────────────


def _relpath(path: Path, start: Path | None = None) -> str:
    """Path relative to *start* (cwd by default) when possible, else absolute."""
    try:
        return os.path.relpath(path, start or Path.cwd())
    except ValueError:  # different drive on Windows
        return str(path)


def _load_raw_config(explicit: str | None, project_dir: Path) -> tuple[Path | None, dict[str, Any]]:
    """Return ``(path, raw_dict)``; ``(None, {})`` means zero-config."""
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise ScheduleInputError(t("cli_config_not_found", path=path))
    else:
        path = resolve_config_path(None, project=project_root(project_dir))
        if not path.is_file():
            return None, {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ScheduleInputError(
            t("cannot_read_file", file_kind="config", path_suffix=f" {path}", error=exc)
        ) from exc
    if not isinstance(raw, dict):
        raise ScheduleInputError(t("cli_config_not_mapping", path=path))
    return path, raw


def _feature_name(tasks_path: Path, explicit: str | None) -> str:
    if explicit:
        return explicit
    name = tasks_path.parent.name
    return name or "feature"


def _brief_context(plan: dict[str, Any], tasks_path: Path) -> BriefContext:
    feature_dir = tasks_path.parent
    spec = feature_dir / "spec.md"
    plan_md = feature_dir / "plan.md"
    return BriefContext(
        feature_name=str(plan.get("feature") or ""),
        spec_path=_relpath(spec) if spec.is_file() else None,
        plan_path=_relpath(plan_md) if plan_md.is_file() else None,
        tasks_path=_relpath(tasks_path),
    )


def _try_images(schedule_json: Path, out_dir: Path, feature: str) -> str | None:
    """Render PNGs via ``solver.visualize``; return the image prefix or None."""
    images_dir = out_dir / "images"
    try:
        from .visualize import main as visualize_main

        rc = visualize_main([str(schedule_json), str(images_dir), "--feature", feature])
    except Exception as exc:  # noqa: BLE001 — images are optional
        log.warning("image rendering skipped: %s", exc)
        return None
    if rc != 0:
        log.warning("image rendering skipped (exit %s)", rc)
        return None
    return f"images/{feature}"


# ───────────────────────────────────────────────────────────────────────
# plan
# ───────────────────────────────────────────────────────────────────────


def cmd_plan(args: argparse.Namespace) -> int:
    tasks_path = Path(args.tasks_md).resolve()
    if not tasks_path.is_file():
        raise ScheduleInputError(t("cli_tasks_not_found", path=tasks_path))
    out_dir = Path(args.out).resolve() if args.out else tasks_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    feature = _feature_name(tasks_path, args.feature)

    cfg_path, raw = _load_raw_config(args.config, tasks_path.parent)
    overrides = {
        k: v
        for k, v in (("workers", args.workers), ("max_tasks_per_worker", args.max_tasks_per_worker))
        if v is not None
    }
    if overrides:
        if raw.get("agents"):
            print(t("cli_agents_override_workers"), file=sys.stderr)
        else:
            raw.update(overrides)

    warnings = WarningCollector()
    parsed = parse_tasks_md(str(tasks_path), raw, warnings)
    if args.verbose:
        parsed["config"]["verbose"] = True
    result = solve_from_json(parsed)

    text = tasks_path.read_bytes()
    result["feature"] = feature
    result["config_path"] = _relpath(cfg_path) if cfg_path else None
    result["source"] = {
        "tasks_md": _relpath(tasks_path, out_dir),
        "sha256": hashlib.sha256(text).hexdigest(),
        "task_ids": [task["id"] for task in parsed["tasks"]],
    }

    print(format_inline_summary(result, feature_name=feature))
    if result.get("status") not in (STATUS_OPTIMAL, STATUS_FEASIBLE):
        return EXIT_NOT_SOLVED  # nothing to run, so nothing is written

    schedule_json = out_dir / SCHEDULE_JSON
    schedule_json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    image_prefix = _try_images(schedule_json, out_dir, feature) if args.images else None
    schedule_md = out_dir / SCHEDULE_MD
    schedule_md.write_text(
        render_markdown(result, feature, image_prefix=image_prefix).rstrip() + "\n",
        encoding="utf-8",
    )
    print()
    print(t("cli_written", files=f"{_relpath(schedule_md)}, {_relpath(schedule_json)}"))
    return EXIT_OK


# ───────────────────────────────────────────────────────────────────────
# next
# ───────────────────────────────────────────────────────────────────────


def _load_plan(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ScheduleInputError(t("cli_plan_not_found", path=path))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ScheduleInputError(t("cli_plan_invalid", path=path, error=exc)) from exc
    if not isinstance(data, dict) or "assignments" not in data:
        raise ScheduleInputError(t("cli_plan_invalid", path=path, error="missing assignments"))
    status = data.get("status")
    if status is not None and status not in (STATUS_OPTIMAL, STATUS_FEASIBLE):
        raise ScheduleInputError(
            t("cli_plan_invalid", path=path, error=f"status {status}, nothing to run")
        )
    return data


def cmd_next(args: argparse.Namespace) -> int:
    schedule_json = Path(args.schedule_json).resolve()
    plan = _load_plan(schedule_json)
    source = plan.get("source") or {}
    if args.tasks_md:
        tasks_path = Path(args.tasks_md).resolve()
    else:
        tasks_path = (schedule_json.parent / str(source.get("tasks_md") or "tasks.md")).resolve()
    if not tasks_path.is_file():
        raise ScheduleInputError(t("cli_tasks_not_found", path=tasks_path))

    plan_ids: list[str] = list(source.get("task_ids") or [a["task_id"] for a in plan["assignments"]])
    checks = scan_checkboxes(tasks_path)
    added = sorted(set(checks) - set(plan_ids))
    removed = sorted(set(plan_ids) - set(checks))
    if added or removed:
        raise ScheduleInputError(
            t(
                "cli_tasks_changed",
                path=_relpath(tasks_path),
                added=", ".join(added) or "-",
                removed=", ".join(removed) or "-",
            )
        )

    done = {tid for tid, is_done in checks.items() if is_done}
    pending = [tid for tid in plan_ids if tid not in done]
    queues = lane_queues(plan["assignments"])
    preds = predecessor_map(plan_ids, plan.get("edges", []), plan.get("resource_edges", []))

    static_rounds = plan.get("rounds", []) or []
    completed_rounds = sum(
        1
        for rnd in static_rounds
        if all(tid in done for lane in rnd.get("lanes", []) for tid in lane.get("tasks", []))
    )
    rnd = next_round(queues, preds, done, index=completed_rounds + 1)
    if rnd is None:
        if args.format == "json":
            print(json.dumps({"status": "done", "done": len(done)}))
        else:
            print(t("cli_all_done", n=len(done)))
        return EXIT_OK

    block = round_to_dict(rnd)
    remaining = len(pending) - len(rnd.task_ids)
    context = _brief_context(plan, tasks_path)
    total = len(static_rounds) or None
    if not rnd.lanes:
        if args.format == "json":
            print(json.dumps({"status": "blocked", **block}, indent=2))
        else:
            print(t("cli_blocked"))
            for b in block["blocked"]:
                print(f"  - {format_blocked(b)}")
        return EXIT_BLOCKED

    if args.format == "json":
        payload: dict[str, Any] = {
            "status": "round",
            "round": block["round"],
            "total_rounds": total,
            "remaining_after": remaining,
            "lanes": [
                {
                    **lane,
                    "brief": render_brief(plan, block, lane, context=context, total_rounds=total),
                }
                for lane in block["lanes"]
            ],
            "blocked": block.get("blocked", []),
        }
        print(json.dumps(payload, indent=2))
    else:
        print(
            render_round(plan, block, context=context, total_rounds=total, remaining_tasks=remaining),
            end="",
        )
    return EXIT_OK


# ───────────────────────────────────────────────────────────────────────
# mark / status
# ───────────────────────────────────────────────────────────────────────


def cmd_mark(args: argparse.Namespace) -> int:
    tasks_path = Path(args.tasks_md)
    if not tasks_path.is_file():
        raise ScheduleInputError(t("cli_tasks_not_found", path=tasks_path))
    ids = list(args.task_ids)
    if args.report:
        text = sys.stdin.read() if args.report == "-" else Path(args.report).read_text(encoding="utf-8")
        report = parse_report(text)
        ids += report["done"]
        if report["failed"]:
            print(t("cli_report_failed", ids=", ".join(report["failed"])), file=sys.stderr)
    if not ids:
        raise ScheduleInputError(t("cli_mark_no_ids"))
    changed = mark_tasks(tasks_path, ids, done=not args.undo)
    print(t("cli_marked", n=changed, state="[ ]" if args.undo else "[x]", path=_relpath(tasks_path)))
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    from .status import main as status_main

    return int(status_main([]))


# ───────────────────────────────────────────────────────────────────────
# argparse
# ───────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m solver",
        description="spec-kit-schedule: plan tasks.md into parallel subagent rounds and drive them.",
    )
    sub = ap.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="tasks.md → schedule.md + schedule.json (+ summary)")
    plan.add_argument("tasks_md", help="Path to the feature's tasks.md")
    plan.add_argument("--config", help="schedule-config.yml (default: .specify/schedule/… or the scaffolded copy under .specify/extensions/schedule/)")
    plan.add_argument("--workers", type=int, help="Parallel subagent lanes (zero-config mode; default 3)")
    plan.add_argument(
        "--max-tasks-per-worker", type=int, dest="max_tasks_per_worker",
        help="Cap tasks per lane; lanes are added if the cap is too small (0 = no cap)",
    )
    plan.add_argument("--feature", help="Feature name for the report (default: tasks.md's directory)")
    plan.add_argument("--out", help="Output directory (default: next to tasks.md)")
    plan.add_argument("--images", action="store_true", help="Also render PNG Gantt/DAG (needs matplotlib)")
    plan.add_argument("-v", "--verbose", action="store_true", help="DEBUG logging + CP-SAT search log")
    plan.set_defaults(func=cmd_plan)

    nxt = sub.add_parser("next", help="Emit the next round of subagent briefs from schedule.json + tasks.md")
    nxt.add_argument("schedule_json", help="Path to schedule.json written by `plan`")
    nxt.add_argument("tasks_md", nargs="?", help="tasks.md (default: the one recorded in schedule.json)")
    nxt.add_argument("--format", choices=("md", "json"), default="md")
    nxt.set_defaults(func=cmd_next)

    mark = sub.add_parser("mark", help="Tick (or untick with --undo) task checkboxes in tasks.md")
    mark.add_argument("tasks_md")
    mark.add_argument("task_ids", nargs="*", metavar="T###")
    mark.add_argument("--undo", action="store_true", help="Set back to [ ]")
    mark.add_argument(
        "--report", metavar="FILE",
        help="Also tick the DONE ids of a subagent report (FILE or - for stdin); FAILED ids stay",
    )
    mark.set_defaults(func=cmd_mark)

    status = sub.add_parser("status", help="Diagnose the installation (see solver.status)")
    status.set_defaults(func=cmd_status)
    return ap


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args, extra = parser.parse_known_args(argv)
    if extra:
        # Python < 3.12 leaves ids after an option unconsumed
        # (``mark tasks.md --undo T002``); they belong to the positional list.
        if args.command == "mark" and not any(tok.startswith("-") for tok in extra):
            args.task_ids = [*args.task_ids, *extra]
        else:
            parser.error(f"unrecognized arguments: {' '.join(extra)}")
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    try:
        return int(args.func(args))
    except ScheduleInputError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    except OSError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_INPUT_ERROR


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
