---
description: "Execute the planned rounds with parallel subagents: one subagent per lane per round, tasks.md checkboxes as the single source of progress. Resumable at any time."
---

# /speckit.schedule.implement — Run the rounds with subagents

User input (optional):

$ARGUMENTS

## What this does

Drives the plan produced by `/speckit-schedule-run` (`schedule.json`)
**round by round**. In every round you launch one subagent per lane,
in parallel, each with a self-contained brief; when all of them return
you record what got done, run the project's checks, and move to the
next round. Progress lives only in the `[x]` checkboxes of `tasks.md`,
so the command can be stopped and resumed at any point.

You (the assistant running this command) are the **orchestrator**. You
are the only writer of `tasks.md` and the only one who runs `git`.

## Preconditions (do these first)

1. Locate the feature: `.specify/scripts/bash/check-prerequisites.sh --json --require-tasks --include-tasks` → `FEATURE_DIR`. Set:

   ```bash
   EXT=".specify/extensions/schedule"; [ -x "$EXT/bin/speckit-schedule" ] || EXT="."
   SKS="$EXT/bin/speckit-schedule"
   PLAN="$FEATURE_DIR/schedule.json"; TASKS="$FEATURE_DIR/tasks.md"
   ```

2. `schedule.json` must exist. If it does not, run `/speckit-schedule-run`
   first (or ask). `next` verifies that `tasks.md` still matches the plan
   and tells you to re-plan otherwise — never edit the plan by hand.
3. Check `git status --short`. If the tree is dirty, tell the user and ask
   whether to continue; do not stash or discard anything.
4. Ask **once**: *"Commit after each round? (default: no — I will stage
   and show the diff)"*. Remember the answer for the whole run.

## The loop

Repeat until `next` prints `DONE`:

1. **Get the round.**

   ```bash
   "$SKS" next "$PLAN" "$TASKS"
   ```

   Exit 0 prints `# Round k of N` with a table (lane → tasks → files)
   followed by one **brief per lane**, or `DONE`. Exit 2 means the
   plan is stale or missing — stop and relay the message. Exit 3 means
   every pending task waits on another pending task — show the blocked
   list and ask the user which tasks are actually complete.

2. **Launch the lanes in parallel.** For every brief, start one subagent
   whose entire prompt is that brief, verbatim. Launch all lanes of the
   round **in the same turn** so they run concurrently:

   - Claude Code: one `Agent` tool call per lane, all in one message.
   - Cursor / Copilot / Gemini CLI: use the platform's subagent or
     background-agent facility the same way.
   - No parallel subagents available: run the lanes yourself one after
     another, each brief in full, in the order listed. The plan stays
     valid — you only lose the concurrency.

   Do not add tasks, reorder tasks, or merge lanes. The file lists in the
   briefs are what keep lanes from colliding.

3. **Collect the reports.** Each subagent ends with:

   ```
   DONE: T001 T002
   FAILED: T003 <reason>      (absent when nothing failed)
   TOUCHED: <paths>
   NOTES: <free text>
   ```

   Read all of them before touching anything. Then cross-check the
   `TOUCHED` lists: any path reported by **two or more lanes** in the
   same round is a possible collision (tasks without a file path are
   invisible to the planner's file mutex). Treat it like a FAILED lane:
   review that file's diff with the user before going on.

4. **Record progress** — only tasks reported DONE:

   ```bash
   "$SKS" mark "$TASKS" T001 T002 ...
   ```

   Or hand over the subagent's reply and let `mark --report reply.txt`
   pick the DONE ids (FAILED ids stay unticked). `mark --undo` reverts a
   tick made by mistake.

5. **Verify the round.** Run the project's test / lint command if one is
   obvious (`plan.md`, `Makefile`, `package.json`, `pyproject.toml`).
   Then `git add -A && git status --short`; if the user opted in, commit
   with a message like `feat(<feature>): round k — T001 T002 …`.

6. **Decide.** If every lane reported DONE and the checks pass, go to
   step 1. Otherwise stop here and report (see below).

## When a round does not fully succeed

Do **not** start the next round. Show the user, per lane: DONE / FAILED
ids, the FAILED reason, any TOUCHED path that was on that lane's "must
NOT edit" list, and any path reported by two lanes. Then offer the three
options and wait:

- **Retry the lane** — re-launch a subagent with the same brief plus the
  failure context (the failed task is still `[ ]`, so `next` will emit
  it again).
- **Fix it by hand** — the user (or you, on request) completes the task,
  then `mark` it and continue with `next`.
- **Skip it** — only if the user says so: `mark` it, note the debt, continue.

If a subagent edited a file from its "must NOT edit" list, or two lanes
reported the same path in `TOUCHED`, review that file's diff with the
user before continuing — the two changes may have overwritten each other.

## Completion

When `next` prints `DONE`:

1. Run the full test suite once more.
2. Summarise: rounds executed, tasks completed (and any skipped), files
   touched, test result, and anything from the NOTES lines that needs a
   human decision.
3. If the git extension is installed, suggest `/speckit-git-commit`.

## Rules of thumb

- Never edit `tasks.md` except through `mark`.
- Never run two rounds at once; never launch a lane whose round is not
  the current one.
- The briefs are complete on purpose — do not summarise them for the
  subagents.
- Resuming later: just run this command again; `next` picks up from the
  checkboxes.
