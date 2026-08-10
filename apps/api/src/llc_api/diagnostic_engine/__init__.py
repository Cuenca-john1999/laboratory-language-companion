"""Deterministic, local-only diagnostic engine.

The package deliberately has no HTTP or model-provider dependencies.  Its
public surface is the strict command/receipt schemas and
``DiagnosticEngineService``.
"""

from llc_api.diagnostic_engine.service import DiagnosticEngineService

__all__ = ["DiagnosticEngineService"]
