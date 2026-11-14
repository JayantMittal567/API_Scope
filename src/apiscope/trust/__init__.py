"""Unified Trust & Evidence Engine package (Task 25).

Provides conservative diagnostic verdicts, cross-diagnostic correlation,
multi-source evidence ingestion, and defensible test trust assessments.
"""

from __future__ import annotations

from apiscope.trust.models import (
    DiagnosticVerdict,
    EvidenceSubsystem,
    OracleProvenance,
    TrustAssessmentReport,
    TrustEvidenceRecord,
    TrustFinding,
    VerdictConfidence,
    VerdictSeverity,
)
from apiscope.trust.service import TrustAssessmentService

__all__ = [
    "DiagnosticVerdict",
    "EvidenceSubsystem",
    "OracleProvenance",
    "TrustAssessmentReport",
    "TrustAssessmentService",
    "TrustEvidenceRecord",
    "TrustFinding",
    "VerdictConfidence",
    "VerdictSeverity",
]
