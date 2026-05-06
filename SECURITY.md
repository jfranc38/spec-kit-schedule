# Security Policy

## Reporting a Vulnerability

Please report security vulnerabilities **privately** via
[GitHub Security Advisories](https://github.com/jfranc38/spec-kit-schedule/security/advisories/new).
Do not open public issues for security reports.

Please include:

1. The affected version (e.g., `0.5.1`) and how it was installed
   (`./bin/install.sh`, `pip install -e .`, release zip).
2. A minimal reproduction: the smallest `tasks.md` + `schedule-config.yml`
   pair (or solver input JSON) that triggers the issue, plus the exact
   command line you ran.
3. The observed behaviour and what you expected instead.
4. If you have a proposed patch, attach it as a unified diff.

Do not include real customer data, credentials, or proprietary task lists
in the report. Anonymise file paths and task descriptions if needed.

## Response Window

- Acknowledgement: within 5 business days of the advisory being filed
  via the GHSA flow above.
- Triage and severity assessment: within 10 business days.
- Patch or mitigation plan: communicated as soon as the impact is
  understood; for high-severity issues we aim for a fix or workaround
  within 30 days of acknowledgement.

If you have not received an acknowledgement within 5 business days,
ping the advisory thread on GitHub; notification settings are the most
likely culprit.

## Embargo and Disclosure

- We prefer coordinated disclosure via the GHSA flow. The default
  embargo is 90 days from acknowledgement, or until a fix is released,
  whichever comes first.
- During the embargo we will not discuss the issue in public channels
  (issues, pull requests, release notes) beyond a generic "security fix"
  reference.
- Once a release is available, we publish a CHANGELOG entry and credit
  the reporter unless they request anonymity.

## Supported Versions

Only the currently shipped minor line receives security fixes. Older
lines are end-of-life on release of the next minor.

| Version line | Status     | Security fixes |
|--------------|------------|----------------|
| `0.5.x`      | Supported  | Yes            |
| `< 0.5.0`    | Pre-release | No            |

If you are running an older snapshot, upgrade to the latest `0.5.x`
release before reporting; the issue may already be fixed.

## Scope

### In scope

- Solver logic in [`solver/`](solver/) (CP-SAT model construction,
  warm-start heuristic, replan, calibration, validation).
- Task and config parsers in `solver/parse_tasks.py` and
  `solver/config_schema.py` (including YAML loading, dependency
  resolution, and the Markdown task grammar).
- Install scripts in [`bin/`](bin/) (`install.sh`, `uninstall.sh`).
- Render and visualize stages in `solver/render_schedule.py`,
  `solver/render_html.py`, and `solver/visualize.py` (path traversal
  in `--image-prefix`, HTML escaping, Mermaid injection).

### Out of scope

- Vulnerabilities in third-party dependencies (Google OR-Tools,
  NetworkX, Plotly, Matplotlib, PyYAML, pydantic, uv). Please report
  those upstream; we will pick up fixes via dependency bumps.
- Issues that require an attacker to already have local code-execution
  on the user's machine, or write access to the project directory.
- Denial-of-service caused by intentionally pathological inputs (huge
  task graphs, adversarial token estimates). The solver has a
  `time_limit` knob for this; see [`docs/determinism.md`](docs/determinism.md).
- Behaviour of MAQA, spec-kit, or any downstream executor that consumes
  `schedule.md`.

## Threat Model and Non-Goals

spec-kit-schedule is research-grade scheduling code. It runs locally,
reads configuration and task files supplied by the user, and emits
artifacts (`schedule.md`, JSON, PNG, HTML) into the user's working
directory. It does not call any LLM API, does not open network
sockets, and does not store credentials.

As a result, the project is **not** a security boundary:

- Confidentiality of inputs (`tasks.md`, `schedule-config.yml`) is the
  user's responsibility. Treat them like any other source file in your
  repository.
- The rendered `schedule.html` embeds Plotly inline. If you publish it,
  apply the same review you would for any committed HTML artifact.
- Running the solver on untrusted task graphs is equivalent to running
  any Python program on untrusted input: keep it inside whatever
  sandbox your workflow already uses.

We will still treat memory-safety issues, command injection, and
arbitrary-write defects as in-scope security bugs, even though the
project is local-only.

Thank you for helping keep the project safe.
