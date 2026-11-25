"""Explicit, stateless RG06-RG08 criteria and report contracts."""

from typing import Any, Literal, Optional, TypedDict

from .contracts import Coverage, Location, Scope, Severity, Status


CorrespondenceRuleId = Literal["RG06", "RG07", "RG08"]


class _CorrespondenceRequest(TypedDict):
    schemaVersion: Literal[1]
    projectId: str
    snapshotId: str
    scope: Scope
    project: dict[str, Any]


class CorrespondenceRequest(_CorrespondenceRequest, total=False):
    correspondenceProfile: dict[str, Any]


class CorrespondenceFinding(TypedDict):
    ruleIds: list[CorrespondenceRuleId]
    status: Status
    severity: Severity
    code: str
    description: str
    location: Location
    evidence: dict[str, Any]


class CorrespondenceRuleResult(TypedDict):
    ruleId: CorrespondenceRuleId
    status: Status
    severity: Severity
    description: str
    checkCounts: dict[Status, int]
    modelPaths: list[str]


class CorrespondenceResult(TypedDict):
    schemaVersion: Literal[1]
    kind: Literal["correspondences"]
    projectId: str
    snapshotId: str
    profileId: Optional[str]
    coverage: Coverage
    rules: list[CorrespondenceRuleResult]
    findings: list[CorrespondenceFinding]
