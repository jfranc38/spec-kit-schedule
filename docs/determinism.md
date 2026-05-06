# Determinism and Reproducibility

This page documents what spec-kit-schedule guarantees about reproducible
output, what it does not, and the knobs you need to set to get a
byte-stable schedule across machines and runs.

The contract is intentionally narrow: optimisation problems with ties
have multiple optimal solutions, and CP-SAT is a parallel solver. Both
of those facts leak into the output unless the user takes specific
steps. The sections below name each leak point and the mitigation.

## The Reproducibility Contract

Given:

1. The same solver input JSON (the output of `solver.parse_tasks`).
2. The same `solver` config block (objective, time_limit, num_workers,
   etc.).
3. The same OR-Tools and Python versions.

spec-kit-schedule will produce the same schedule **only when the
configuration eliminates the parallel-search non-determinism described
below.** The default configuration does not — `num_workers` defaults to
`8`. See "OR-Tools internals" for the fix.

The parser, validator, warm-start heuristic, and renderer are
deterministic on identical inputs.

## Tie-Breaking in the Warm-Start Heuristic

`solver.scheduler.list_schedule_heuristic` builds a feasible initial
schedule by topologically sorting the precedence graph and ordering
tasks by:

```
(earliest_start_time, story_priority, task_id_index)
```

(see `solver/scheduler.py` around lines 422–506.) The third component is
the task's positional index in the input — i.e., the order in which
tasks appear in `tasks.md`. Two consequences:

- **Input order matters for ties.** If two tasks have the same
  earliest-start and the same `story_priority`, the one that appears
  first in `tasks.md` is scheduled first. Reordering the markdown
  reorders the warm-start hint, which can change which optimal solution
  CP-SAT settles on when there are multiple equally-optimal answers.
- **Stable across re-runs.** As long as `tasks.md` is byte-identical,
  the heuristic produces the same priority list. There is no random
  component in the heuristic itself.

If you care about a stable schedule across edits to `tasks.md`, prefer
appending tasks rather than inserting them, and set explicit
`story_priority` values to break ties in a way you control.

## OR-Tools Internals: `num_workers` and Parallel Search

CP-SAT uses portfolio search: with `num_workers = N`, it runs `N`
distinct strategies in parallel and returns the first optimal proof.
Which worker proves optimality first depends on OS scheduling and is
inherently non-deterministic. The default in
`solver/defaults.py` is `NUM_WORKERS = 8`.

To make a run reproducible:

```yaml
solver:
  num_workers: 1
  time_limit: 60       # raise if your problem is large
  warm_start: true
  symmetry_breaking: true
```

With `num_workers: 1` the solver runs a single deterministic search
strategy. The tradeoff is wall time: on hard instances a single worker
may take much longer to prove optimality, or hit the `time_limit` and
return the best feasible solution found so far. For small features
(≤ 30 tasks) the difference is usually negligible.

Note: the project does not currently expose `random_seed` as a
`SolverConfig` field. If you need to randomise tie-breaking inside
CP-SAT you would need to extend `SolverConfig` and pass the value
through to `solver.parameters.random_seed` at the call site in
`_run_solver` (`solver/scheduler.py`). The default OR-Tools seed is
fixed, so single-worker runs are already reproducible without it.

## Path-Normalisation Caveat

`solver.validation.normalize_path` runs `posixpath.normpath` after
converting backslashes to forward slashes. That is enough to merge
`./src/a.py`, `src/a.py`, and `src/./a.py` into the same key for
file-mutex grouping.

It is **not** enough to merge two callers that use different relative
bases for the same logical file. `posixpath.normpath` does not resolve
`..` against an absolute root, so:

- `tasks/../src/a.py` normalises to `src/a.py`.
- `/repo/src/a.py` normalises to `/repo/src/a.py`.
- `/repo/tasks/../src/a.py` normalises to `/repo/src/a.py` (the `..`
  collapses against `/repo/tasks`, which is fine here).
- But `src/a.py` and `/repo/src/a.py` are treated as **different**
  files even when they refer to the same on-disk path.

If two tasks reference the same file under different relative bases,
the solver will not detect the mutex and may schedule them in
parallel — silently producing a write race in the executor.

**Mitigation.** Canonicalise paths upstream of the parser. Two safe
strategies:

1. Always express `file_paths` as repository-relative paths from a
   single, well-known root (e.g., the repo root). Document this in
   your project's tasks template.
2. If you cannot, run a pre-pass that resolves each path against the
   repo root with `os.path.relpath(os.path.realpath(p), repo_root)`
   before handing the task list to `parse_tasks`.

This is a known limitation rather than a bug: `parse_tasks` does not
have access to the user's repository root and cannot make this
decision on its behalf.

## Other Determinism-Adjacent Notes

- **Stochastic durations.** When `token_std_dev > 0`, the calibration
  module runs Monte Carlo sampling. That sampling uses a fixed seed,
  so calibrate output is reproducible per input, but changing the
  input regenerates the sample.
- **Replan.** `solver.replan` freezes completed and in-flight tasks at
  their observed start/end and re-solves the residual graph. The
  residual solve obeys the same `num_workers` rule above; pin it to
  `1` if you need byte-stable replans.
- **Renderer output.** `render_schedule.py` is deterministic given the
  solver output JSON. PNG output from `visualize.py` depends on the
  matplotlib version and font rendering; treat the PNGs as derived
  artifacts and diff the markdown or JSON instead.

## Checklist for Reproducing a Run

If you need to reproduce a schedule exactly (e.g., to file a bug, run a
benchmark, or compare two configurations), do the following:

1. Pin Python and dependency versions. The committed `uv.lock` does
   this for development; for ad-hoc runs note the OR-Tools version
   from `uv pip show ortools`.
2. Set `solver.num_workers: 1` in your config.
3. Set an explicit `solver.time_limit` long enough for the run to
   reach `OPTIMAL` (check the rendered `schedule.md` Status field; if
   it says `FEASIBLE` you hit the limit).
4. Keep `tasks.md` byte-identical between runs. Even reordering
   non-conflicting tasks can shuffle tie-breaking.
5. Capture the parser output (`solver.parse_tasks ... > in.json`) and
   the solver output (`solver.scheduler < in.json > out.json`); diff
   `out.json` between runs. The renderer is deterministic, so two
   matching `out.json` files imply two matching `schedule.md` files.
6. Record the command line and environment. `make schedule` runs the
   full pipeline against the bundled example and is a good sanity
   check that your environment is set up correctly.

If two runs of step 5 disagree with the configuration above, that is
a bug worth reporting — see [`SECURITY.md`](../SECURITY.md) for the
reporting channel if you suspect a security-relevant defect, or open
an issue otherwise.
