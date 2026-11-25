"""Ephemeral traceability reports. No persisted request or history is implied."""

from typing import Any, Literal, Optional, TypedDict

from .contracts import Coverage, Location, Severity, Status


TraceRuleId = Literal["RG20", "RG21", "RG22"]


class TraceFinding(TypedDict):
    ruleIds: list[TraceRuleId]
    status: Status
    severity: Severity
    code: str
    description: str
    location: Location
    evidence: dict[str, Any]


class TraceRuleResult(TypedDict):
    ruleId: TraceRuleId
    status: Status
    severity: Severity
    description: str
    checkCounts: dict[Status, int]
    modelPaths: list[str]


class TraceResult(TypedDict):
    schemaVersion: Literal[1]
    kind: Literal["traceability"]
    projectId: str
    snapshotId: str
    beforeSnapshotId: str
    attemptId: Optional[str]
    contextId: Optional[str]
    coverage: Coverage
    traceCoverage: dict[str, Any]
    traces: list[dict[str, Any]]
    rules: list[TraceRuleResult]
    findings: list[TraceFinding]
