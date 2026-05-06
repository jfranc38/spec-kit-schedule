# Formal Mathematical Formulation

## Problem Classification

**Multi-Skill Resource-Constrained Project Scheduling Problem (MS-RCPSP)**
with cardinality-bounded agents, context-budget constraints, file-mutex
disjunctive resources, and lexicographic multi-objective.

In Graham three-field notation, the present problem is closest to

$$Q \,\big|\, M_i, \text{prec}, \text{disj-file}, \kappa, C \,\big|\, \text{lex}\bigl(C_{\max},\, L_{\max}\bigr)$$

(uniform parallel machines with skill-based eligibility, precedence DAG,
file-disjunctive resource shadows, per-agent cardinality and context caps;
lexicographic objective).

Specialises (one skill per task, vs. multi-skill *tasks* in the cited works)
and augments (cardinality cap $\kappa_a$, context budget $C_a$, file-mutex
disjunctives) Bellenguez-Morineau & Néron (2007) and Myszkowski et al.
(2015).

---

## Sets

| Symbol | Definition |
|--------|-----------|
| $T = \{1, \ldots, n\}$ | Set of tasks extracted from `tasks.md` |
| $A = \{1, \ldots, m\}$ | Set of heterogeneous AI agents |
| $\mathbb{S} = \{1, \ldots, L\}$ | Set of skills (e.g., `python`, `test`, `design`) |
| $E \subseteq T \times T$ | Precedence arcs (DAG edges) |
| $F$ | Set of all files touched by any task |
| $F_i \subseteq F$ | File footprint of task $i$ |
| $S_a \subseteq \mathbb{S}$ | Skill set of agent $a$ |
| $\mathcal{E}_i = \{a \in A : s_i \in S_a\}$ | Compatible agents for task $i$ |

---

## Parameters

| Symbol | Definition |
|--------|-----------|
| $s_i \in \mathbb{S}$ | Required skill for task $i$ |
| $c_i \in \mathbb{Z}_{\geq 0}$ | Estimated token cost of task $i$ |
| $\pi_i \in \{1,2,3,\ldots\}$ | Story priority of task $i$ (1 = highest) |
| $p_{ia} \in \mathbb{Z}_{\geq 0}$ | Processing time of task $i$ on agent $a$ |
| $\sigma_i \in \mathbb{R}_{\geq 0}$ | Token standard deviation for task $i$ |
| $q \in (0, 1)$ | Stochastic quantile for duration substitution (default 0.5) |
| $\rho_a \in \mathbb{R}_{> 0}$ | Price per 1 K tokens for agent $a$ (cost-aware objective) |
| $\kappa_a \in \mathbb{Z}_{> 0}$ | Cardinality cap (max tasks) for agent $a$ |
| $C_a \in \mathbb{Z}_{> 0}$ | Context budget (max tokens) for agent $a$ |
| $\texttt{par}_i \in \{0,1\}$ | 1 if task $i$ has the `[P]` parallel-safe flag |
| $H$ | Scheduling horizon (upper bound on makespan) |

---

## Decision Variables

| Symbol | Domain | Definition |
|--------|--------|-----------|
| $x_{ia}$ | $\{0,1\}$ | 1 iff task $i$ is assigned to agent $a$ |
| $S_i$ | $[0, H]$ | Start time of task $i$ |
| $E_i$ | $[0, H]$ | End time of task $i$ |
| $d_i$ | $[\min_{a \in \mathcal{E}_i} p_{ia},\, \max_{a \in \mathcal{E}_i} p_{ia}]$ | Duration of task $i$ (channelled by C3) |
| $\mathrm{iv}_i$ | Interval | Master interval $\mathrm{Interval}(S_i, d_i, E_i)$ — always present |
| $\mathrm{iv}_{ia}$ | Optional interval | Per-agent interval of task $i$ on agent $a$, present iff $x_{ia} = 1$ |
| $L_a$ | $[0, H]$ | Total processing load on agent $a$ |
| $N_a$ | $[0, n]$ | Number of tasks assigned to agent $a$ |
| $C_{\max}$ | $[0, H]$ | Makespan |
| $L_{\max}$ | $[0, H]$ | Maximum agent load |

---

## Constraints

### C0 — Non-preemption

Each task occupies a single contiguous interval $[S_i, E_i]$. Preemption,
splitting, and migration between agents are not modelled.

### C1 — Unique Assignment

$$\sum_{a \in \mathcal{E}_i} x_{ia} = 1 \quad \forall\, i \in T$$

### C2 — Skill Eligibility

$$x_{ia} = 0 \quad \forall\, i \in T,\; a \notin \mathcal{E}_i$$

(Enforced structurally by excluding infeasible pairs.)

### C3 — Duration Channeling

$$x_{ia} = 1 \implies d_i = p_{ia} \quad \forall\, i \in T,\; a \in \mathcal{E}_i$$

### C4 — Interval Consistency

$$E_i = S_i + d_i \quad \forall\, i \in T$$

### C5 — DAG Precedence

$$E_i \leq S_j \quad \forall\, (i,j) \in E$$

### C6 — Per-Agent Disjunctive (Unary Machine)

$$\texttt{NoOverlap}\bigl(\{\mathrm{iv}_{ia} : i \in T\}\bigr) \quad \forall\, a \in A$$

Each agent processes at most one task at a time.

### C7 — File Mutex

$$\texttt{NoOverlap}\bigl(\{\mathrm{iv}_i : i \in T,\; f \in F_i,\; \texttt{par}_i = 0\}\bigr) \quad \forall\, f \in F$$

Non-`[P]` tasks writing the same file cannot overlap across *any* agents.
Posted over the master intervals $\mathrm{iv}_i$, so the disjunction is
agent-agnostic.

### C8 — Cardinality Cap (Hallucination Guardrail)

$$N_a = \sum_{i \in T} x_{ia} \leq \kappa_a \quad \forall\, a \in A$$

### C9 — Context Budget (Context-Rot Guardrail)

$$\sum_{i \in T} c_i \cdot x_{ia} \leq C_a \quad \forall\, a \in A$$

### C10 — Load Definitions

$$L_a = \sum_{i \in T} p_{ia} \cdot x_{ia} \quad \forall\, a \in A$$

### C11 — Makespan and Max-Load Definitions

$$C_{\max} \geq E_i \quad \forall\, i \in T$$
$$L_{\max} \geq L_a \quad \forall\, a \in A$$

### C12 — Symmetry Breaking (Optional)

Define an equivalence relation on $A$:

$$a \sim a' \iff \bigl(S_a, \kappa_a, C_a, \text{speed}_a, \rho_a\bigr) = \bigl(S_{a'}, \kappa_{a'}, C_{a'}, \text{speed}_{a'}, \rho_{a'}\bigr).$$

For each equivalence class $\{a_1, \ldots, a_q\}$ (indices sorted ascending),
post

$$L_{a_1} \geq L_{a_2} \geq \cdots \geq L_{a_q}.$$

The price $\rho_a$ is included because under the cost-aware objective two
agents differing only in $\rho$ are *not* interchangeable: reassigning across
them changes $\mathit{TotalCost}$. See Sherali & Smith (2001).

**Lemma (C12 optimality preservation).** Given any feasible schedule and an
equivalence class $\{a_1, \ldots, a_q\}$ as above, the schedule obtained by
reassigning tasks among the class to enforce $L_{a_1} \geq \cdots \geq L_{a_q}$
is feasible (the data is invariant under the relabelling permutation, so all
constraints are preserved) and has the same objective value (every objective
term — $C_{\max}$, $L_{\max}$, $\mathit{TotalCost}$ — is symmetric under
permutations within an equivalence class). Hence C12 does not eliminate any
optimum. $\square$

Under objective $f$, including a feature $g$ in the equivalence relation is
necessary iff $f$ depends on $g$. The chosen tuple
$(S_a, \kappa_a, C_a, \text{speed}_a, \rho_a)$ is minimal: skills $S_a$ are
required for feasibility (C2); $\kappa_a$ and $C_a$ bound feasibility (C8, C9);
$\text{speed}_a$ governs $p_{ia}$ and hence $L_a$ (C10); $\rho_a$ governs
$\mathit{TotalCost}$ (cost-aware mode). Other agent attributes do not enter
any objective term.

---

## Objective

Three modes are available via the `objective` config key.

### Lexicographic (default — `objective: lexicographic`)

$$\text{lex-}\min\;\bigl(C_{\max},\; L_{\max}\bigr)$$

Implemented as a two-phase solve:

1. **Phase 1**: $\min C_{\max}$ subject to C1–C12. Let $C_{\max}^* =$ optimal value.
2. **Phase 2**: Add $C_{\max} = C_{\max}^*$, then $\min L_{\max}$. The equality
   pin tightens bound propagation versus the inequality form.

Phase 2 is warm-started with Phase 1's variable values via `add_hint`.

### Weighted Alternative (`objective: weighted`)

$$\min\; W \cdot C_{\max} + L_{\max}$$

where $W > \max_{\text{feasible}} L_{\max}$ ensures makespan dominance. Single-phase solve.

### Cost-Aware (`objective: cost_aware`)

$$\text{lex-}\min\;\bigl(C_{\max},\; \mathit{TotalCost},\; L_{\max}\bigr)$$

The implementation is a three-phase pinned solve:

1. **Phase 1**: $\min C_{\max} \to C_{\max}^*$.
2. **Phase 2**: add $C_{\max} = C_{\max}^*$, then $\min \mathit{TotalCost} \to \mathit{cost}^*$.
3. **Phase 3**: add $\mathit{TotalCost} = \mathit{cost}^*$, then $\min L_{\max}$.

CP-SAT minimises only linear integer expressions, so monetary costs are
scaled by $\Sigma = 10^4$ to preserve four-decimal-place precision:

$$\mathit{TotalCost}_{\text{int}} = \sum_{i \in T} \sum_{a \in \mathcal{E}_i} \mathrm{round}\!\left(\frac{\rho_a \cdot c_i}{1000} \cdot \Sigma\right) \cdot x_{ia}, \qquad \Sigma = 10^4.$$

`round(...)` is Python's built-in `round` (banker's rounding,
round-half-to-even). The `_COST_SCALE` $= 10^4$ factor preserves four
decimal places of dollar precision; the rounding-direction quirk is bounded
by $5 \times 10^{-5}$ USD per task.

The user-facing `total_cost` field is recomputed in USD from the optimal
assignment as $\sum_{i} (c_i \cdot \rho_{a^*(i)}) / 1000$, where $a^*(i)$
is the chosen agent. The CP-SAT optimisation and the reported USD figure
agree to within $n \cdot 10^{-4}$ USD over all $n$ tasks (per-task
scale-rounding error bounded by $10^{-4}$).

To stay safely within int64 bounds for the cumulative scaled cost expression,
the solver caps individual token estimates at $10^8$; projects up to roughly
500 tasks at typical token counts remain well under the int64 headroom of
$2^{62}$. Requires `price_per_1k_tokens` on each agent config entry.

This formulation follows the lexicographic / $\varepsilon$-constraint method
(Ehrgott 2005, ch. 4) — equality pinning of higher-priority objectives is
equivalent to setting $\varepsilon$ to the optimal value of the prior phase.
Phase pinning is the $\varepsilon$-constraint method with $\varepsilon$ set
to the prior phase's optimum (Ehrgott 2005, ch. 4). The 3-phase pinned solve
is equivalent to lex-min on the objective vector
$(C_{\max},\, \mathit{TotalCost},\, L_{\max})$.

---

## Stochastic Durations

Tasks may carry an optional token standard deviation $\sigma_i \geq 0$. The
solver computes a deterministic effective token count

$$\mathrm{eff}_i = \max\bigl(0,\; \Phi^{-1}(q;\, \mu_i, \sigma_i)\bigr)$$

where $\mu_i = c_i$ is the mean estimate and $q \in (0, 1)$ is the configured
quantile (default $q = 0.5$, the median). The domain is open at 1 because
$\Phi^{-1}(1)$ is unbounded — setting $q = 1$ would yield an infinite
duration; practical risk-averse choices use $q \in [0.5, 0.95]$. Processing
time is then

$$p_{ia} = \left\lceil \frac{\lceil \mathrm{eff}_i / \mathrm{unit} \rceil}{\text{speed}_a} \right\rceil.$$

This is **not** a robust or chance-constrained formulation — it is
sensitivity analysis via deterministic-quantile substitution. Setting
$q > 0.5$ inflates durations to a risk-averse percentile but provides no
probabilistic guarantee on makespan or feasibility.

`solver/calibrate.py` is a separate offline tool that ingests historical
execution logs (`runs.jsonl`) and updates per-agent `speed_factor` estimates
and per-complexity token means via an exponentially-weighted moving average,

$$\theta_{\text{new}} = \theta_{\text{old}} + \alpha \,(\theta_{\text{raw}} - \theta_{\text{old}}), \qquad \alpha \in [0, 1],$$

with median ratios $\theta_{\text{raw}} = \mathrm{median}_k(\hat{p}_k / p_k)
\cdot \theta_{\text{old}}$. It does **not** simulate the schedule.

A robust counterpart (e.g., chance-constrained or CVaR-bounded) is not
currently implemented; see Artigues et al. (2013) for the standard
formulation.

---

## Replanning

`solver.replan` implements online re-optimisation after partial execution.
Given a *frozen* set $F^{\text{frz}} \subseteq T$ of tasks with prior
assignments $\{(a^{\text{frz}}_i,\, S^{\text{frz}}_i,\, d^{\text{frz}}_i) :
i \in F^{\text{frz}}\}$:

1. **Pin** for each $i \in F^{\text{frz}}$:
   $$x_{i, a^{\text{frz}}_i} = 1, \qquad S_i = S^{\text{frz}}_i, \qquad d_i = d^{\text{frz}}_i.$$
   Each pin is an equality constraint; frozen durations override the duration
   channelling that would otherwise tie $d_i$ to $p_{i, a^{\text{frz}}_i}$.
2. **Validate**: if any $a^{\text{frz}}_i \notin \mathcal{E}_i$ in the
   current portfolio, raise `ScheduleInputError`.
3. **Residual task set** $T' = T \setminus F^{\text{frz, completed}}$.
   In-flight tasks remain in $T'$ but carry the frozen pins.
4. **Horizon expansion**: $H' = \max\bigl(H,\, \max_{i \in F^{\text{frz}}}(S^{\text{frz}}_i + d^{\text{frz}}_i)\bigr)$.
5. **Re-solve** CP-SAT on $(T',\, E \cap (T' \times T'))$ with the frozen
   pins and (optionally) prior-solution hints on the unfrozen tasks.

---

## Hallucination Calibration

The cardinality cap $\kappa_a$ and context budget $C_a$ are empirically
calibrated. Calibration is by **capability tier** rather than by specific
model: the underlying long-context degradation curves (RULER, NoLiMa) test
models across providers — Anthropic, OpenAI, Google, Meta, Mistral — and
report broadly similar context-length patterns within each tier. Map your
portfolio's agents to the closest tier below and override individual
`kappa` / `context_budget` values when you have provider-specific
calibration data.

| Tier | Examples | $\kappa_a$ | $C_a$ (tokens) | Rationale |
|------|----------|-----------|-----------------|-----------|
| Frontier | Claude Opus 4, GPT-4o, Gemini 2.0 Pro, o1 | 6 | 32K | RULER: top-tier models retain ≥80% accuracy at 32K context |
| Mid | Claude Sonnet 4, GPT-4o-mini, Gemini 2.0 Flash, GPT-4 Turbo | 10 | 16K | NoLiMa-style benchmarks show stable performance below ~16K |
| Small | Claude Haiku 3.5, Mistral Small, Llama 3 70B, GPT-3.5 | 15 | 8K | Coding-task degradation past ~8K commonly reported across small open- and closed-source models |

These hard inequalities are **not** a linear approximation of the underlying
quality function; they are conservative *feasibility cuts* — a guardrail
envelope approximating the level set $\{q \geq q^*\}$ of a *conceptual*
quality model (not used directly by the solver):

$$q(i, a, k, L) = \varphi_a \cdot h(L) \cdot g(k),$$

with $h(L) = (1 + L/L_0)^{-\beta}$ (context degradation) and
$g(k) = k^{\alpha}$ (position-dependent fatigue). Symbols:
$\varphi_a$ — agent-intrinsic baseline accuracy in $(0, 1]$;
$L$ — cumulative tokens in the agent's context;
$k$ — task position in the agent's queue (1-indexed);
$L_0 > 0$, $\beta > 0$ — scale and shape of the context-degradation curve;
$\alpha < 0$ — position-fatigue exponent (so $k^{\alpha}$ decreases in $k$).

These parameters are descriptive and not estimated by the solver; $\kappa_a$
and $C_a$ act as conservative envelopes bounding $L$ and $k$ inside regimes
where empirical accuracy stays acceptable. Embedding the non-linear $q$
directly would push the model from CP-SAT into MINLP; the box-constraint
envelope preserves linearity while excluding regions of accuracy collapse.

### Multi-provider portfolios

The model is provider-agnostic: each agent declares its `provider` and
`model` strings purely as metadata. The solver consumes only the
schedule-relevant fields (`skills`, `kappa`, `context_budget`,
`speed_factor`, `price_per_1k_tokens`, optional `token_estimates`). A
single portfolio can therefore mix runners such as:

- Anthropic API (`provider: anthropic`, `model: claude-opus-4`)
- OpenAI API (`provider: openai`, `model: gpt-4o`)
- Google API (`provider: google`, `model: gemini-2.0-pro`)
- GitHub Copilot (`provider: github`, `model: gpt-4o`)
- Cursor's multi-provider setup (`provider: cursor`, `model: <auto>`)
- Local inference (`provider: ollama`, `model: llama-3-70b`)

The user's editor or CLI is responsible for actually invoking the model
named in each agent's `model` field; the solver only optimises the
assignment.

---

## CP-SAT Encoding Notes

1. **Master + optional intervals**: follows the `flexible_job_shop_sat.py`
   pattern from OR-Tools (Perron & Didier 2024). One master `IntervalVar`
   $\mathrm{iv}_i$ per task shares its `start` and `end` with $|\mathcal{E}_i|$
   optional `FixedSizeIntervalVar` instances $\mathrm{iv}_{ia}$, linked via
   `AddExactlyOne` over presence literals $x_{ia}$. The shared-start
   construction is sound and tighter than a per-agent-only encoding because
   propagation on $S_i$ feeds through to every alternative simultaneously.

2. **NoOverlap vs. Cumulative**: `AddNoOverlap` (disjunctive) is used
   throughout, since each agent is a unary machine and file-mutex over
   non-`[P]` tasks is also unary. `AddCumulative` is unnecessary.

3. **Horizon computation**:
   $$H = \max\bigl(\lceil \text{mult} \cdot \mathrm{LB} \rceil,\; H_{\text{heur}},\; H_{\text{serial}}\bigr),$$
   where
   - $\mathrm{LB} = \max(L_{\text{cp}}, L_{\text{load}}, L_{\text{mutex}})$ is
     the strongest of three lower bounds: critical-path length $L_{\text{cp}}$
     (node-weighted longest path in the precedence DAG, computed via
     `networkx` topological DP); amortised per-agent load
     $L_{\text{load}} = \lceil \sum_i p_i^{\min} / m \rceil$; and the heaviest
     file-mutex chain $L_{\text{mutex}} = \max_f \sum_{i \in \text{group}(f)} p_i^{\min}$.
   - $H_{\text{heur}}$ is the makespan of the warm-start greedy heuristic,
     a true upper bound on the optimum.
   - $H_{\text{serial}} = \sum_i \max_{a \in \mathcal{E}_i} p_{ia}$ is the
     totally-serial fallback when the heuristic fails to produce a complete
     assignment.
   - $\text{mult}$ defaults to $1.5$ and acts as a multiplicative *safety
     margin* on top of an already-valid upper bound.

   **Lemma (horizon validity).** Given preflight passes (per-skill aggregate
   token demand fits aggregate budget, per-skill task count fits aggregate
   $\kappa$), and at least one compatible agent per task has $\kappa \geq 1$
   and $C \geq c_i$, a totally-serial schedule exists with makespan
   $\leq H_{\text{serial}}$. The serial UB is a value bound in all cases; the
   constructibility of the serial schedule depends on these preflight
   invariants. $H_{\text{heur}}$ is, by construction, the makespan of a
   feasible schedule (when the heuristic completes) and therefore a valid
   upper bound on $C_{\max}^*$. Hence
   $H \geq \max(H_{\text{heur}}, H_{\text{serial}}) \geq C_{\max}^*$, and the
   CP-SAT model with horizon $H$ is infeasibility-equivalent to the original
   problem: every feasible schedule fits within $[0, H]$. $\square$

   The `max(..., 1)` floor handles the degenerate $n = 0$ case (empty task
   set), where $\mathrm{lb}_{\max} = H_{\text{serial}} = 0$. CP-SAT requires
   non-zero IntVar domains; $H \geq 1$ is the minimal valid horizon.

4. **Warm-start**: a greedy priority-rule heuristic seeds the solver.
   Priorities are earliest-start times from a `networkx` topological DP;
   assignment picks the compatible agent with the earliest available time
   while respecting $\kappa$, $C$, and file-mutex windows. When no compatible
   greedy assignment exists for some task (e.g., all eligible agents are
   $\kappa$-saturated under the partial schedule), the heuristic emits a
   *partial* hint covering only the tasks it could place; CP-SAT accepts
   partial hints as a valid starting point. Empirically 2–5× speedup on
   200+ task instances. The heuristic mirrors precedence (C5), per-agent
   disjunctive (C6), file-mutex (C7), cardinality cap (C8), context budget
   (C9), skill eligibility (C2), and the symmetry-class load ordering (C12).
   It does not anticipate the full CP-SAT propagation, so its assignments
   are advisory hints — CP-SAT validates and discards infeasibilities
   silently.

5. **Critical-path extraction**: after the final phase, the *realised-schedule
   graph* — original precedence edges plus induced resource arcs for
   same-agent consecutivity and same-file order — is constructed, and the
   node-weighted longest path is computed via
   `networkx.lexicographical_topological_sort`. The chain equals the makespan
   for schedules without load-balance slack; otherwise it is a tight lower
   bound and the dominant driver of total time.

---

## Result Schema and Optimality Reporting

The top-level `result.status` is `"OPTIMAL"` only when **every** phase that
ran proved optimality (CP-SAT status `OPTIMAL`). If any phase returned
`FEASIBLE` (timed out before proving optimality), `result.status` is
`"FEASIBLE"` and `result.stats.final_gap` reports the relative MIP gap

$$\text{gap} = \frac{|\,\text{obj} - \text{best\_bound}\,|}{|\text{obj}|}$$

from CP-SAT for the most recent timed-out phase. The schedule is feasible
but not proven lex-optimal.

For `lexicographic` mode, the relevant phases are 1 and 2. For `cost_aware`,
phases 1, 2, 3. For `weighted` (single-phase), only phase 1.

Note: `intermediate` records improving incumbents from `_AnytimeCallback`;
it does NOT capture lower-bound improvements between incumbents. CP-SAT's
bound-improvement curve is therefore not visible here.

---

## Computational Complexity

The classical RCPSP is **strongly NP-hard** (Blazewicz, Lenstra & Rinnooy
Kan, 1983, [DOI:10.1016/0166-218X(83)90012-4](https://doi.org/10.1016/0166-218X(83)90012-4)),
and so are all of its multi-skill extensions (Bellenguez-Morineau & Néron
2007). Strong NP-hardness of the present formulation can be shown by
restriction:

- **Without cardinality / budget caps and file mutexes**, set $|A| = m$
  identical agents with $S_a = \mathbb{S}$, $\kappa_a = \infty$, $C_a = \infty$,
  $E = \varnothing$. The residual problem is $P\,\|\,C_{\max}$
  (parallel-identical-machine makespan minimisation). When $m$ is part of
  the input (as in our portfolio configuration), this is **strongly
  NP-hard** via reduction from 3-PARTITION (Garey & Johnson 1979, problem
  SS8). For any fixed $m \geq 2$ the problem remains NP-hard (PARTITION
  reduction at $m = 2$).
- **Even with a single agent** ($m = 1$, $E = \varnothing$,
  $\kappa_a = \infty$), feasibility under the context budget $C_a$ is
  equivalent to the classical 0/1 *subset-sum / PARTITION* feasibility
  question on the multiset $\{c_i\}_{i \in T}$, which is NP-complete
  (Karp 1972). Adding the cardinality cap $\kappa_a$ and a precedence
  DAG only enlarges the constraint set, so the decision version of the
  full problem is at least NP-complete.

Hence the formulation is **strongly NP-hard** when the portfolio size $m$
is treated as input (first reduction), and at minimum **NP-hard** for any
fixed portfolio (second). The file-mutex constraint C7 is a disjunctive
resource over master intervals; removing it ($F_i = \varnothing$ for all
$i$) preserves both reductions, so it can only make the problem harder.

Empirically, on spec-kit projects in the 50–500 task / 2–8 agent regime,
CP-SAT finds optimal or near-optimal solutions within the 60-second
default time limit; see [Empirical Benchmarks](#empirical-benchmarks).

---

## Empirical Benchmarks

The benchmarks compare the CP-SAT solver against a pure-Python greedy
baseline (MAQA-style first-available-agent) which processes tasks in
topological order and assigns each to the eligible agent with the earliest
available time, respecting $\kappa$ and context-budget constraints. The
**Gap%** column reports `(greedy − cpsat) / greedy × 100`: positive means
CP-SAT found a shorter schedule, zero means both methods agree.

Benchmarks are not stored in the spec because hardware variance and
OR-Tools version drift make committed numbers stale. Run `make bench` to
regenerate `benchmarks/results/latest.md` (Markdown table) and the JSON
timeseries under `benchmarks/results/`. Methodology in `benchmarks/README.md`.

---

## References

- Artigues, C., Leus, R., & Talla Nobibon, F. (2013). Robust optimization for resource-constrained project scheduling with uncertain activity durations. *Flexible Services and Manufacturing Journal*, 25(1–2), 175–205. [DOI:10.1007/s10696-012-9156-1](https://doi.org/10.1007/s10696-012-9156-1).
- Bellenguez-Morineau, O., & Néron, E. (2007). A branch-and-bound method for solving multi-skill project scheduling problem. *RAIRO - Operations Research*, 41(2), 155–170. [DOI:10.1051/ro:2007020](https://doi.org/10.1051/ro:2007020).
- Blazewicz, J., Lenstra, J. K., & Rinnooy Kan, A. H. G. (1983). Scheduling subject to resource constraints: Classification and complexity. *Discrete Applied Mathematics*, 5(1), 11–24. [DOI:10.1016/0166-218X(83)90012-4](https://doi.org/10.1016/0166-218X(83)90012-4).
- Ehrgott, M. (2005). *Multicriteria Optimization* (2nd ed.). Springer. ISBN 978-3-540-21398-7.
- Garey, M. R., & Johnson, D. S. (1979). *Computers and Intractability: A Guide to the Theory of NP-Completeness*. W. H. Freeman. ISBN 978-0-7167-1045-5.
- Hsieh, C.-P., et al. (2024). RULER: What's the real context size of your long-context language models? [*arXiv:2404.06654*](https://arxiv.org/abs/2404.06654).
- Karp, R. M. (1972). Reducibility among combinatorial problems. In *Complexity of Computer Computations*, 85–103. Plenum. [DOI:10.1007/978-1-4684-2001-2_9](https://doi.org/10.1007/978-1-4684-2001-2_9).
- Modarressi, A., et al. (2025). NoLiMa: Long-context evaluation beyond literal matching. [*arXiv:2502.05167*](https://arxiv.org/abs/2502.05167).
- Myszkowski, P. B., Skowroński, M. E., Olech, Ł. P., & Oślizło, K. (2015). Hybrid differential evolution and greedy algorithm for multi-skill resource-constrained project scheduling problem. *Applied Soft Computing*, 37, 652–669. [DOI:10.1016/j.asoc.2015.09.024](https://doi.org/10.1016/j.asoc.2015.09.024).
- Perron, L., & Didier, F. (2024). CP-SAT solver. Google OR-Tools. [https://developers.google.com/optimization/cp/cp_solver](https://developers.google.com/optimization/cp/cp_solver).
- Sherali, H. D., & Smith, J. C. (2001). Improving discrete model representations via symmetry considerations. *Management Science*, 47(10), 1396–1407. [DOI:10.1287/mnsc.47.10.1396.10265](https://doi.org/10.1287/mnsc.47.10.1396.10265).
