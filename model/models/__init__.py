"""The models of MODELS section 5, one file each.

They share the signal (`sim.candidate_edges`), the execution (`sim.execute`)
and the accounting (`sim.metrics`); what differs is the constraint each one
puts on the book it builds, which is the only thing this pass is measuring.

Model D (`maker`) is deliberately absent. MODELS 5.4 defers it: a resting bid
in a book this thin is filled mostly by someone who has just watched the
underlying move against it, and simulating that honestly needs the trade tape,
a cancel rule and a queue model.
"""
from __future__ import annotations

from models.direction_balanced import DirectionBalanced
from models.edge_taker import EdgeTaker
from models.scenario_hedged import ScenarioHedged

__all__ = ["EdgeTaker", "DirectionBalanced", "ScenarioHedged"]
