"""Audit result schemas and validation."""

from pydantic import BaseModel
from typing import Optional


class AuditCheck(BaseModel):
    """Single audit check result."""
    check_name: str  # e.g., "boundary_violation", "test_coverage"
    severity: str  # "info", "warning", "error"
    message: str
    remediation: Optional[str] = None


class AuditRecommendation(BaseModel):
    """Recommendation from auditor."""
    priority: str  # "low", "medium", "high"
    suggestion: str


class TokenUsage(BaseModel):
    """Token usage for an audit run."""
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class AuditResult(BaseModel):
    """What auditor returns to caller."""
    passed: bool
    status: str  # "passed" | "failed" | "warned"
    issues: list[AuditCheck] = []
    recommendations: list[AuditRecommendation] = []
    token_usage: TokenUsage
    duration_seconds: float

    def validate(self) -> bool:
        """Ensure result is well-formed."""
        if not isinstance(self.passed, bool):
            return False
        if self.status not in ["passed", "failed", "warned"]:
            return False
        return True


def validate_audit_result(result: AuditResult) -> tuple[bool, Optional[str]]:
    """
    Validate auditor result is complete and consistent.
    Returns: (is_valid, error_message)
    """
    if not result.validate():
        return False, "Audit result schema invalid"

    # If passed, should have no failed checks
    failed = len([i for i in result.issues if i.severity == "error"])
    if result.status == "passed" and failed > 0:
        return False, "Status is 'passed' but error-severity issues reported"

    # If failed, should have at least one failed check
    if result.status == "failed" and failed == 0:
        return False, "Status is 'failed' but no error-severity issues reported"

    return True, None
