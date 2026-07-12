"""Deterministic, provider-independent learning rules for DeutschOS."""

from deutschos_api.learning_engine.curriculum import CURRICULUM, CURRICULUM_VERSION
from deutschos_api.learning_engine.scoring import ENGINE_VERSION, AttemptOutcome

__all__ = ["CURRICULUM", "CURRICULUM_VERSION", "ENGINE_VERSION", "AttemptOutcome"]
