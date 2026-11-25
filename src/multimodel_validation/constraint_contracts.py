"""Stateless RG09-RG12 contracts, separate from structural validation."""

from typing import Any, Literal, Optional, TypedDict

from .contracts import Coverage, Location, Scope, Severity, Status


ConstraintRuleId = Literal["RG09", "RG10", "RG11", "RG12"]


class _ConstraintRequest(TypedDict):
    schemaVersion: Literal[1]
    projectId: str
    snapshotId: str
    scope: Scope
    project: dict[str, Any]


class ConstraintRequest(_ConstraintRequest, total=False):
    constraintProfile: dict[str, Any]


class ConstraintFinding(TypedDict):
    ruleIds: list[ConstraintRuleId]
    status: Status
    severity: Severity
    code: str
    description: str
    location: Location
    evidence: dict[str, Any]


class ConstraintRuleResult(TypedDict):
    ruleId: ConstraintRuleId
    status: Status
    severity: Severity
    description: str
    checkCounts: dict[Status, int]
    modelPaths: list[str]


class ConstraintResult(TypedDict):
    schemaVersion: Literal[1]
    kind: Literal["constraints"]
    projectId: str
    snapshotId: str
    profileId: Optional[str]
    coverage: Coverage
    rules: list[ConstraintRuleResult]
    findings: list[ConstraintFinding]
