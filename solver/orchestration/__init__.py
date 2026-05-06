"""Solver orchestration: phase loops over the CP-SAT model.

This package owns the lexicographic, cost-aware, and weighted solve loops,
plus the per-phase machinery (anytime callback, status recording, infeasibility
diagnostics). It depends on the model bundle (``solver.scheduler``/
``solver.model.build``) and on result extraction (``solver.result.extract``).
"""
