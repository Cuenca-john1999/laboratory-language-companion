"""Versioned, local pedagogical content contracts and loaders."""

from deutschos_api.content.diagnostic import (
    DiagnosticBankManifest,
    DiagnosticContentError,
    DiagnosticContentFormatError,
    DiagnosticOptionDefinition,
    DiagnosticTaskBank,
    DiagnosticTaskDefinition,
    DiagnosticTaskDefinitionV2,
    EditorialStatus,
    FilesystemCandidateProvider,
    load_diagnostic_banks,
)
from deutschos_api.content.diagnostic_reachability import (
    DiagnosticReachabilityReport,
    ReachabilityIssue,
    ReachabilityIssueCode,
    analyze_diagnostic_bank,
)

__all__ = [
    "DiagnosticBankManifest",
    "DiagnosticContentError",
    "DiagnosticContentFormatError",
    "DiagnosticOptionDefinition",
    "DiagnosticReachabilityReport",
    "DiagnosticTaskBank",
    "DiagnosticTaskDefinition",
    "DiagnosticTaskDefinitionV2",
    "EditorialStatus",
    "FilesystemCandidateProvider",
    "ReachabilityIssue",
    "ReachabilityIssueCode",
    "analyze_diagnostic_bank",
    "load_diagnostic_banks",
]
