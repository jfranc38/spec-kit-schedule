"""Post-solve result building for spec-kit-schedule.

This package owns extraction of CP-SAT solver output into the public result
shape (assignments, waves, critical path, agent summary). It depends only on
the model dataclasses; it does not import the orchestration layer.
"""
