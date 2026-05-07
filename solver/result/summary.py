"""Inline schedule summary — compact, terminal-safe headline numbers.

After ``/speckit.schedule.run`` finishes, the user gets a long ``schedule.md``
to scan. ``format_inline_summary`` renders the headline numbers (status,
makespan, agent utilisation, top critical-path waves, total cost) into a
plain-text block the agent prints BEFORE the user opens any file. Pure
function: no IO, no logging, no side effects. Defensive against missing
keys so an unexpected result shape never raises.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

__all__ = ["format_inline_summary"]


def _money(v: float) -> str:
    """4 dp for sub-dollar (signal precision), 2 dp + thousands sep otherwise."""
    return f"${v:.4f}" if abs(v) < 1.0 else f"${v:,.2f}"


def _agent_label(ag: dict[str, Any]) -> str:
    aid, model = str(ag.get("agent_id", "?")), str(ag.get("model", "?"))
    prov = ag.get("provider")
    return f"{aid} ({model} · {prov})" if prov else f"{aid} ({model})"


def _utilization(agents: list[dict[str, Any]]) -> list[str]:
    if not agents:
        return ["  (no agents)"]
    rows = sorted(
        agents, key=lambda a: (-int(a.get("task_count", 0)), str(a.get("agent_id", "")))
    )
    labels = [_agent_label(a) for a in rows]
    w = max(len(s) for s in labels)
    return [
        f"  {lbl:<{w}}  {int(round(float(ag.get('kappa_utilization', 0.0)))):>3d}% κ  | "
        f"{int(ag.get('task_count', 0)):>2d} tasks | "
        f"{int(ag.get('total_tokens', 0)):>9,} tok | "
        f"{_money(float(ag.get('cost', 0.0))):>10s}"
        for ag, lbl in zip(rows, labels, strict=False)
    ]


def _critical_waves(waves: list[dict[str, Any]], crit: set[str]) -> list[str]:
    """Top 3 waves: prefer those touching the critical path, then by wall time."""
    enriched: list[tuple[int, int, dict[str, Any]]] = []
    for w in waves:
        tasks = w.get("tasks", []) or []
        if not tasks:
            continue
        wall = max(int(t.get("duration", 0)) for t in tasks)
        on = sum(1 for t in tasks if t.get("task_id") in crit)
        enriched.append((on, wall, w))
    if not enriched:
        return ["  (no waves)"]
    enriched.sort(key=lambda r: (-r[0], -r[1], int(r[2].get("wave", 0))))
    rows: list[tuple[str, str, int]] = []
    for _, wall, w in enriched[:3]:
        tasks = w.get("tasks", []) or []
        ids = ", ".join(str(t.get("task_id", "?")) for t in tasks)
        skills = Counter(str(t.get("required_skill", "?")) for t in tasks)
        dom = skills.most_common(1)[0][0] if skills else "?"
        rows.append((f"Wave {w.get('wave', '?')} ({ids}):", dom, wall))
    head_w = max(len(r[0]) for r in rows)
    return [f"  {h:<{head_w}}  {wt:>3d} tu    [{sk}]" for h, sk, wt in rows]


def _cost_lines(
    agents: list[dict[str, Any]], total: float, cost_aware: bool
) -> list[str]:
    if not cost_aware:
        return [f"Total cost:  {_money(total)} (cost-aware mode off)"]
    contributors = sorted(
        (a for a in agents if float(a.get("cost", 0.0)) > 0.0),
        key=lambda a: -float(a.get("cost", 0.0)),
    )
    out: list[str] = []
    if contributors and total > 0:
        parts = [
            f"{_money(float(a.get('cost', 0.0)))} ({a.get('agent_id', '?')}, "
            f"{round(float(a.get('cost', 0.0)) / total * 100)}%)"
            for a in contributors
        ]
        out.append(f"Cost split:  {' + '.join(parts)} = {_money(total)}")
    out.append(f"Total cost:  {_money(total)} (cost-aware mode on)")
    return out


def _optimal(result: dict[str, Any], header: str) -> list[str]:
    stats = result.get("stats", {}) or {}
    agents = result.get("agent_summary", []) or []
    waves = result.get("waves", []) or []
    crit = set(result.get("critical_path", []) or [])
    total_agents = stats.get("total_agents", len(agents))
    active = sum(1 for a in agents if int(a.get("task_count", 0)) > 0)
    total_cost = float(stats.get("total_cost", result.get("total_cost", 0.0)) or 0.0)
    # phase3_status is the cost-aware sentinel: lex stops at 2 phases, cost_aware runs 3.
    cost_aware = "phase3_status" in stats
    lines = [
        header,
        f"Status:    {result.get('status', '?')}",
        f"Makespan:  {stats.get('makespan', result.get('makespan', '?'))} time units",
        f"Waves:     {stats.get('total_waves', len(waves))}",
        f"Agents:    {total_agents} ({total_agents - active} idle, {active} active)",
    ]
    gap = stats.get("final_gap")
    if gap is not None and float(gap) > 0.0:
        lines.append(
            f"Gap:       {float(gap) * 100.0:.1f}% "
            "(anytime mode — increase time_limit for tighter bound)"
        )
    lines += ["", "Agent utilization (descending):", *_utilization(agents)]
    lines += ["", "Critical-path waves (top 3 by total wall time):", *_critical_waves(waves, crit)]
    lines += ["", *_cost_lines(agents, total_cost, cost_aware)]
    solve_time = stats.get("total_solve_time")
    if solve_time is not None:
        lines.append(f"Total solve time: {float(solve_time):.2f} s")
    lines += ["", "Full report: schedule.md", "Next: /speckit.implement to execute"]
    return lines


def _infeasible(result: dict[str, Any], header: str) -> list[str]:
    msg = str(result.get("message") or "model is infeasible at the configured horizon")
    return [
        header,
        "Status:    INFEASIBLE",
        "",
        "Diagnostic:",
        *(f"  {c}" for c in (msg.splitlines() or [msg])),
        "",
        "Common fixes:",
        "  - widen agent skills / add an agent",
        "  - increase context_budget or kappa",
        "  - raise horizon_multiplier or time_limit",
    ]


def format_inline_summary(result: dict[str, Any], *, feature_name: str = "") -> str:
    """Render a compact human-readable summary of a schedule result.

    ``result`` is the dict returned by ``solve_from_json``. ``feature_name``
    is rendered in the header when non-empty. Always returns a non-empty
    multi-line string suitable for printing to a terminal.
    """
    name = feature_name.strip() if feature_name else ""
    header = f"═══ Schedule — {name} ═══" if name else "═══ Schedule ═══"
    status = str(result.get("status", "UNKNOWN")).upper()
    if status in ("OPTIMAL", "FEASIBLE"):
        lines = _optimal(result, header)
    elif status == "INFEASIBLE":
        lines = _infeasible(result, header)
    else:
        lines = [
            header,
            f"Status:    {status}",
            "",
            "No assignments produced. Re-run with --verbose for solver detail.",
        ]
    return "\n".join(lines)
