# Formal Mathematical Formulation

## Problem Classification

**Multi-Skill Resource-Constrained Project Scheduling Problem (MS-RCPSP)**
with cardinality-bounded agents, context-budget constraints, file-mutex
disjunctive resources, and lexicographic bi-objective.

Extends: Bellenguez-Morineau & Néron (2007), Myszkowski et al. (2015).

---

## Sets

| Symbol | Definition |
|--------|-----------|
| $T = \{1, \ldots, n\}$ | Set of tasks extracted from `tasks.md` |
| $A = \{1, \ldots, m\}$ | Set of heterogeneous AI agents |
| $S = \{1, \ldots, L\}$ | Set of skills (e.g., `python`, `test`, `design`) |
| $E \subseteq T \times T$ | Precedence arcs (DAG edges) |
| $F$ | Set of all files touched by any task |
| $F_i \subseteq F$ | File footprint of task $i$ |
| $S_a \subseteq S$ | Skill set of agent $a$ |
| $\mathcal{E}_i = \{a \in A : s_i \in S_a\}$ | Compatible agents for task $i$ |

---

## Parameters

| Symbol | Definition |
|--------|-----------|
| $s_i \in S$ | Required skill for task $i$ |
| $c_i \in \mathbb{Z}_{\geq 0}$ | Estimated token cost of task $i$ |
| $\pi_i \in \{1,2,3,\ldots\}$ | Story priority of task $i$ (1 = highest) |
| $p_{ia} \in \mathbb{Z}_{\geq 0}$ | Processing time of task $i$ on agent $a$ |
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
| $d_i$ | $[\min_a p_{ia}, \max_a p_{ia}]$ | Duration of task $i$ (channeled) |
| $\mathrm{iv}_{ia}$ | Optional interval | Interval of task $i$ on agent $a$, present iff $x_{ia} = 1$ |
| $L_a$ | $[0, H]$ | Total processing load on agent $a$ |
| $N_a$ | $[0, n]$ | Number of tasks assigned to agent $a$ |
| $C_{\max}$ | $[0, H]$ | Makespan |
| $L_{\max}$ | $[0, H]$ | Maximum agent load |

---

## Constraints

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

For agents $a, a'$ with identical $(S_a, \kappa_a, C_a, \text{speed})$:

$$L_a \geq L_{a'}$$

---

## Objective

### Lexicographic (default)

$$\text{lex-}\min\;\bigl(C_{\max},\; L_{\max}\bigr)$$

Implemented as two-phase solve:

1. **Phase 1**: $\min C_{\max}$ subject to C1–C12. Let $C_{\max}^* = $ optimal value.
2. **Phase 2**: Add $C_{\max} \leq C_{\max}^*$, then $\min L_{\max}$.

Phase 2 is warm-started with Phase 1's solution via `add_hint`.

### Weighted Alternative

$$\min\; W \cdot C_{\max} + L_{\max}$$

where $W > \max_{\text{feasible}} L_{\max}$ ensures makespan dominance.

---

## Hallucination Calibration

The cardinality cap $\kappa_a$ and context budget $C_a$ are empirically calibrated:

| Agent Class | $\kappa_a$ | $C_a$ (tokens) | Rationale |
|-------------|-----------|-----------------|-----------|
| Large (Opus) | 6 | 32K | RULER: ≥85% accuracy up to 32K |
| Medium (Sonnet) | 10 | 16K | NoLiMa: safe zone below 16K |
| Small (Haiku) | 15 | 8K | Chroma: coding tasks degrade sharply past 8K |

These serve as a **linear proxy** for the non-linear quality function:

$$q(i, a, k, L) = \varphi_a \cdot h(L) \cdot g(k)$$

where $h(L) = (1 + L/L_0)^{-\beta}$ captures context degradation and $g(k) = k^{\alpha}$ captures position-dependent learning/fatigue.

---

## CP-SAT Encoding Notes

1. **Master + Optional intervals**: Follows the `flexible_job_shop_sat.py` pattern from OR-Tools. One master `IntervalVar` per task linked to $m$ optional `FixedSizeIntervalVar` via `AddExactlyOne` over presence literals.

2. **NoOverlap vs Cumulative**: We use `AddNoOverlap` (disjunctive) rather than `AddCumulative` because each agent is a unary machine. File-mutex also uses `AddNoOverlap` over master intervals.

3. **Horizon computation**: $H = \lceil \text{mult} \times \max(L_{\text{cp}}, L_{\text{load}}, L_{\text{mutex}}) \rceil$ where $L_{\text{cp}}$ is the critical-path length (node-weighted longest path in the precedence DAG, computed via `networkx` topological DP), $L_{\text{load}} = \lceil \sum_i p^{\min}_i / m \rceil$ the amortised per-agent load bound, and $L_{\text{mutex}} = \max_f \sum_{i \in \text{group}(f)} p^{\min}_i$ the heaviest file-mutex chain. The multiplier defaults to 1.5 and is configurable; it protects against load-balancing infeasibility without inflating the search space unnecessarily.

4. **Warm-start**: A greedy priority-rule heuristic seeds the solver. Priorities are earliest-start times from a `networkx` topological DP; assignment picks the compatible agent with the earliest available time while respecting $\kappa$, $C$, and file-mutex windows. Because the heuristic honours every hard constraint, CP-SAT accepts the hint as a feasible incumbent instead of discarding it. Empirically 2–5× speedup on 200+ task instances.

5. **Critical path extraction**: After Phase 2, we construct the *realised-schedule graph* — original precedence edges plus induced resource arcs for same-agent consecutivity and same-file order — then compute the node-weighted longest path via `networkx.lexicographical_topological_sort`. The chain equals the makespan for schedules without Phase-2 load-balance slack; otherwise it is a tight lower bound and the dominant driver of total time.

---

## Computational Complexity

The MS-RCPSP is **NP-hard** in the strong sense (Blazewicz et al., 1983). The file-mutex constraints add disjunctive resource interactions. For practical spec-kit projects (50–500 tasks, 2–8 agents), CP-SAT finds optimal or near-optimal solutions within the 60-second time limit.

---

## References

- Bellenguez-Morineau, O., & Néron, E. (2007). A branch-and-bound method for solving multi-skill project scheduling problem. *RAIRO - Operations Research*, 41(2), 155–170.
- Myszkowski, P.B., Skowroński, M.E., Olech, Ł.P., & Oślizło, K. (2015). Hybrid differential evolution and greedy algorithm for multi-skill resource-constrained project scheduling problem. *Applied Soft Computing*, 37, 652–669.
- Hsieh, C.-P., et al. (2024). RULER: What's the real context size of your long-context language models? *arXiv:2404.06654*.
- Modarressi, A., et al. (2025). NoLiMa: Long-context evaluation beyond literal matching. *arXiv:2502.05167*.
- Perron, L., & Didier, F. (2024). OR-Tools CP-SAT solver. Google.
