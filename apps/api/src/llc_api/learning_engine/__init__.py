"""Deterministic, provider-independent learning rules for LLC."""

from llc_api.learning_engine.curriculum import CURRICULUM, CURRICULUM_VERSION
from llc_api.learning_engine.scoring import ENGINE_VERSION, AttemptOutcome

__all__ = ["CURRICULUM", "CURRICULUM_VERSION", "ENGINE_VERSION", "AttemptOutcome"]
