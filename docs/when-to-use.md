# When to Use spec-kit-schedule

CP-SAT is powerful, but it is also the largest dependency this project
pulls in. This page exists to help you decide whether the optimisation
machinery is worth its overhead for your specific feature, or whether a
simple greedy assignment will already give you the schedule you want.

The short version: spec-kit-schedule earns its keep when the constraints
genuinely interact. If your tasks line up cleanly on a single agent
with no file conflicts, the heuristic baseline will match the optimal
solution and you save yourself the OR-Tools dependency.

## Decision Table

The recommendation column assumes a typical development feature with
moderate parallelism. "Greedy" means MAQA's built-in first-available
heuristic or any equivalent list scheduler. "Lex" is the default
`objective: lexicographic` mode. "Cost-aware" is `objective: cost_aware`
with `price_per_1k_tokens` set on each agent.

| Tasks | Agents | Has costs? | File mutex? | Heterogeneous κ / context? | Recommendation                  |
|------:|-------:|:----------:|:-----------:|:--------------------------:|---------------------------------|
|  ≤ 10 |    1   | no         | no          | n/a                        | Built-in greedy                 |
|  ≤ 10 |   2–3  | no         | no          | no                         | Built-in greedy                 |
|  ≤ 10 |   2–3  | no         | yes         | yes                        | spec-kit-schedule lex           |
| 11–30 |   2–4  | no         | yes         | yes                        | spec-kit-schedule lex           |
| 11–30 |   2–4  | yes        | any         | yes                        | spec-kit-schedule cost-aware    |
| 31–80 |   3–6  | any        | any         | any                        | spec-kit-schedule lex (or cost) |
|  > 80 |   ≥ 4  | any        | any         | any                        | spec-kit-schedule, raise `time_limit` |

The table is a heuristic, not a contract. The benchmarks under
`benchmarks/` exercise the same regimes — see
[`benchmarks/README.md`](../benchmarks/README.md) and
`benchmarks/results/latest.md` for measured numbers on your hardware.

## Signs spec-kit-schedule is the right call

- **Heterogeneous agents.** Different `speed_factor`, `kappa`, or
  `context_budget` per agent. A greedy first-fit will pick the wrong
  agent any time the cheapest-now choice creates a downstream
  bottleneck.
- **Hard κ caps.** You are willing to refuse extra work on an agent
  rather than overrun its empirical hallucination threshold. The
  solver enforces the cap as a constraint; greedy ignores it unless
  you bolt on extra logic.
- **File-mutex needs.** Multiple non-`[P]` tasks write to the same
  file. The solver serialises them across agents while still
  parallelising everything else; greedy either over-serialises or
  silently produces a write race.
- **Cost / quality tradeoffs.** You have a mix of cheap and premium
  models and want the cheapest assignment that still achieves optimal
  makespan. That is exactly the lexicographic chain in `cost_aware`.
- **Replanning a partial run.** You have started executing and a few
  tasks overran their estimate. `solver.replan` freezes completed
  and in-flight assignments and re-solves the residual graph; a
  greedy redo would shuffle tasks already in motion.
- **Critical-path insight.** You want the makespan-driving chain
  surfaced explicitly so you know which task to optimise first. The
  rendered `schedule.md` includes a Critical Path table; greedy
  output does not.

## Signs greedy is enough

- **Single agent.** With one agent the schedule is just a topological
  order of the DAG; both approaches produce the same output.
- **No costs and no κ pressure.** All agents are interchangeable, no
  agent is at risk of saturating its kappa or context budget.
- **No file-mutex requirements.** Every task is `[P]` or touches
  disjoint files.
- **No precedence cross-cuts.** The DAG is wide and shallow with no
  long-range dependencies. Greedy gets the same makespan because
  there are no tradeoffs to make.
- **You need a schedule in milliseconds.** The CP-SAT solve, even on
  small inputs, is dominated by OR-Tools import time (see below).
  Greedy returns instantly.
- **You are already inside MAQA.** MAQA's coordinator already does a
  reasonable greedy pass. Pulling in this extension is only worth it
  for the constraints listed in the previous section.

## The Cost of Pulling in OR-Tools

Adding spec-kit-schedule to a project means accepting Google OR-Tools
as a transitive dependency. Concretely:

- **Wheel size.** The `ortools` wheel ships native binaries and is
  noticeably larger than a pure-Python dependency. Expect the install
  step (`uv sync` or `pip install -e .`) to dominate the first-run
  time on a clean machine.
- **Import time.** `from ortools.sat.python import cp_model` is not
  free. For one-shot CLI use this shows up as latency on every solver
  invocation, which is why short greedy runs feel snappier even when
  the optimisation itself is microseconds.
- **Platform coverage.** OR-Tools provides wheels for the major
  glibc-Linux, macOS (x86_64 + arm64), and Windows targets. If your
  environment is musl-Linux or an exotic architecture you may need to
  build from source; the greedy alternative has no such constraint.
- **Solver knobs.** The default `num_workers` is `8`; on a constrained
  CI runner you may want to lower it via the `solver.num_workers`
  config field. See [`docs/determinism.md`](determinism.md) for the
  reproducibility implications of that setting.

## Pointers

- The full feature list and quick-start commands live in
  [`README.md`](../README.md).
- The formal MS-RCPSP model is in [`docs/formulation.md`](formulation.md).
- Benchmarks comparing the lex and cost-aware solver configurations
  against the greedy baseline are under
  [`benchmarks/`](../benchmarks/); refresh them with `make bench`.
- Reproducibility guarantees and known sources of non-determinism are
  documented in [`docs/determinism.md`](determinism.md).

If after reading this you are still unsure, the safest path is to run
the smoke test (`./bin/install.sh`) on the bundled example, then point
the parser at your own `tasks.md` with `--verbose` and inspect the
warnings the solver surfaces. If they are all about κ caps,
file-mutex, or makespan-vs-cost tradeoffs, you are in the right
neighbourhood for spec-kit-schedule.
