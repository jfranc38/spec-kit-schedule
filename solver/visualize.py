#!/usr/bin/env python3
"""Static image renderers for schedule output.

Mermaid is the primary visualisation because it lives in `schedule.md`
and renders on GitHub, but some consumers (slides, printed reports, IDEs
without Mermaid) need a regular PNG/SVG. This module emits those from
the same solver JSON, with an optional `viz` dependency extra.

Usage:
    python -m solver.visualize <solver_output.json> <out_dir>
        [--feature NAME] [--format png|svg|pdf] [--dpi 150]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "solver"  # noqa: A001

import networkx as nx

from .defaults import CRITICAL_COLOR, CRITICAL_HATCH, palette_for
from .model.result_types import (
    ScheduleResult,  # noqa: F401  (schema doc — see render_*() docstrings)
)

__all__ = ["render_dag", "render_gantt", "main"]


_FALLBACK_AGENT_COLOR = "#666666"
_NORMAL_EDGE_COLOR = "#888888"
_NODE_BORDER_COLOR = "#222222"
_LABEL_COLOR = "#FFFFFF"

_DEFAULT_DPI = 150
_NODE_SIZE = 900
_LABEL_FONT_SIZE = 8
_GANTT_TEXT_SIZE = 7
_CRITICAL_LINEWIDTH = 3.0
_NORMAL_LINEWIDTH = 1.0
_CRITICAL_EDGE_WIDTH = 2.5
_NORMAL_EDGE_WIDTH = 1.0
_GANTT_BAR_HEIGHT = 0.55


def _hierarchical_layout(graph: nx.DiGraph) -> dict[Any, Any]:
    """Layered left-to-right layout mimicking graphviz `dot`.

    Each node's layer is its longest-path distance from any source; the
    node gets a `_layer` attribute and the actual placement is delegated
    to `nx.multipartite_layout`, which handles the ordering within layers
    and the horizontal/vertical spacing.
    """
    if len(graph) == 0:
        return {}
    for u in nx.topological_sort(graph):
        preds = list(graph.predecessors(u))
        graph.nodes[u]["_layer"] = 1 + max(
            (graph.nodes[p]["_layer"] for p in preds),
            default=-1,
        )
    # ``nx.multipartite_layout`` is annotated to return ``Any``; reify the
    # result so mypy --strict's ``no-any-return`` rule sees a concrete dict.
    layout: dict[Any, Any] = nx.multipartite_layout(graph, subset_key="_layer", align="vertical")
    return layout


def _require_matplotlib() -> None:
    try:
        import matplotlib.pyplot  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "matplotlib is required for solver.visualize. Install with "
            "`uv sync --extra viz` or `pip install 'matplotlib>=3.7,<4'`."
        ) from exc


def render_dag(
    data: dict[str, Any],
    output: Path,
    *,
    dpi: int = 150,
) -> Path:
    """Write a coloured DAG to `output`, one colour per agent.

    The expected shape of ``data`` is :class:`solver.model.result_types.ScheduleResult`.
    """
    _require_matplotlib()
    import matplotlib.pyplot as plt

    parser_edges = [tuple(e) for e in data.get("edges", [])]
    resource_edges = [tuple(e) for e in data.get("resource_edges", [])]
    critical_path_edges = [tuple(e) for e in data.get("critical_path_edges", [])]
    assignments = data.get("assignments", [])
    critical_path = set(data.get("critical_path", []))

    graph = nx.DiGraph()
    agent_for: dict[str, str] = {}
    for a in assignments:
        graph.add_node(a["task_id"], agent_id=a["agent_id"])
        agent_for[a["task_id"]] = a["agent_id"]
    graph.add_edges_from(parser_edges)
    graph.add_edges_from(resource_edges)
    # Guarantee every critical-path arc is drawn, even if the solver emitted
    # neither in `edges` nor in `resource_edges` for some reason. This is
    # the invariant the user cares about: if a task is marked critical, the
    # arrow into it must be visible.
    graph.add_edges_from(critical_path_edges)

    _, color_by_agent = palette_for(assignments)

    pos = _hierarchical_layout(graph)

    critical_edge_set = set(critical_path_edges)
    node_colors = [
        color_by_agent.get(agent_for.get(n, ""), _FALLBACK_AGENT_COLOR) for n in graph.nodes
    ]
    edgecolors = [CRITICAL_COLOR if n in critical_path else _NODE_BORDER_COLOR for n in graph.nodes]
    linewidths = [
        _CRITICAL_LINEWIDTH if n in critical_path else _NORMAL_LINEWIDTH for n in graph.nodes
    ]

    # Width scales with layer count, height with the widest layer, so
    # portraits of 500-task graphs don't crush labels into a tiny strip.
    layer_counts: dict[Any, int] = {}
    for n in graph.nodes:
        layer_counts[graph.nodes[n].get("_layer", 0)] = (
            layer_counts.get(graph.nodes[n].get("_layer", 0), 0) + 1
        )
    width = max(12, len(layer_counts) * 1.8)
    height = max(6, max(layer_counts.values(), default=1) * 0.6)
    fig, ax = plt.subplots(figsize=(width, height))
    # Draw a red halo behind critical nodes so the highlight survives
    # even when the agent colour is close to the critical red.
    critical_nodes = [n for n in graph.nodes if n in critical_path]
    if critical_nodes:
        nx.draw_networkx_nodes(
            graph,
            pos,
            nodelist=critical_nodes,
            node_color=CRITICAL_COLOR,
            node_size=int(_NODE_SIZE * 1.6),
            edgecolors=CRITICAL_COLOR,
            linewidths=0.0,
            ax=ax,
        )
    nx.draw_networkx_nodes(
        graph,
        pos,
        node_color=node_colors,
        edgecolors=edgecolors,
        linewidths=linewidths,
        node_size=_NODE_SIZE,
        ax=ax,
    )
    normal_edges = [e for e in graph.edges if e not in critical_edge_set]
    if normal_edges:
        nx.draw_networkx_edges(
            graph,
            pos,
            edgelist=normal_edges,
            edge_color=_NORMAL_EDGE_COLOR,
            arrows=True,
            arrowsize=12,
            ax=ax,
        )
    if critical_edge_set:
        nx.draw_networkx_edges(
            graph,
            pos,
            edgelist=list(critical_edge_set),
            edge_color=CRITICAL_COLOR,
            width=_CRITICAL_EDGE_WIDTH,
            arrows=True,
            arrowsize=16,
            ax=ax,
        )
    nx.draw_networkx_labels(
        graph,
        pos,
        font_size=_LABEL_FONT_SIZE,
        font_color=_LABEL_COLOR,
        ax=ax,
    )

    legend_handles = [
        plt.Line2D([0], [0], marker="o", color="w", markerfacecolor=c, markersize=10, label=ag)  # type: ignore[attr-defined,unused-ignore]
        for ag, c in color_by_agent.items()
    ]
    if critical_path:
        legend_handles.append(
            plt.Line2D(  # type: ignore[attr-defined,unused-ignore]
                [0], [0], color=CRITICAL_COLOR, lw=_CRITICAL_EDGE_WIDTH, label="critical path"
            )
        )
    ax.legend(handles=legend_handles, loc="best", fontsize=8, frameon=True)
    ax.set_title(f"Dependency DAG — makespan {data.get('stats', {}).get('makespan', '?')}")
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(output, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return output


def render_gantt(
    data: dict[str, Any],
    output: Path,
    *,
    dpi: int = 150,
) -> Path:
    """Write a horizontal Gantt chart (one row per agent) to `output`.

    The expected shape of ``data`` is :class:`solver.model.result_types.ScheduleResult`.
    """
    _require_matplotlib()
    import matplotlib.pyplot as plt

    assignments = data.get("assignments", [])
    critical_path = set(data.get("critical_path", []))
    stats = data.get("stats", {})
    makespan = stats.get("makespan", 0)

    agents_sorted, color_by_agent = palette_for(assignments)
    row = {ag: i for i, ag in enumerate(agents_sorted)}

    fig, ax = plt.subplots(figsize=(max(10, makespan / 15), max(3, len(agents_sorted) * 0.7)))
    for a in assignments:
        y = row[a["agent_id"]]
        is_crit = a["task_id"] in critical_path
        ax.barh(
            y,
            a["end"] - a["start"],
            left=a["start"],
            height=_GANTT_BAR_HEIGHT,
            color=color_by_agent[a["agent_id"]],
            edgecolor=CRITICAL_COLOR if is_crit else _NODE_BORDER_COLOR,
            linewidth=2.5 if is_crit else 0.5,
            # Redundant cue: hatching survives even when the agent fill
            # colour already contains red, which would otherwise hide the
            # critical border.
            hatch=CRITICAL_HATCH if is_crit else None,
        )
        ax.text(
            a["start"] + (a["end"] - a["start"]) / 2,
            y,
            a["task_id"],
            ha="center",
            va="center",
            fontsize=_GANTT_TEXT_SIZE,
            color=_LABEL_COLOR,
        )

    ax.set_yticks(list(row.values()))
    ax.set_yticklabels(list(row.keys()))
    ax.set_xlabel("Time units")
    ax.set_xlim(0, max(makespan, 1) * 1.02)
    ax.invert_yaxis()
    ax.grid(axis="x", linestyle=":", alpha=0.4)
    ax.set_title(f"Schedule Gantt — makespan {makespan}")
    fig.tight_layout()
    fig.savefig(output, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return output


def _build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="solver.visualize",
        description="Render solver output as DAG + Gantt images.",
    )
    ap.add_argument("input", help="Path to solver_output.json")
    ap.add_argument("outdir", help="Directory for output images (created if missing)")
    ap.add_argument("--format", default="png", choices=("png", "svg", "pdf"))
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--feature", default="schedule", help="Filename stem")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)
    with open(args.input, encoding="utf-8") as f:
        data = json.load(f)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    dag_path = outdir / f"{args.feature}-dag.{args.format}"
    gantt_path = outdir / f"{args.feature}-gantt.{args.format}"
    render_dag(data, dag_path, dpi=args.dpi)
    render_gantt(data, gantt_path, dpi=args.dpi)
    print(f"wrote {dag_path}", file=sys.stderr)
    print(f"wrote {gantt_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
