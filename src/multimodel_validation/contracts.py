from typing import Any, Literal, Optional, TypedDict


RuleId = Literal["RG01", "RG02", "RG03", "RG04", "RG05"]
Status = Literal["cumple", "incumple", "no_evaluable", "no_aplica"]
Scope = Literal["complete", "partial"]
Severity = Literal["info", "warning", "error"]


class _Snapshot(TypedDict):
    schemaVersion: Literal[1]
    projectId: str
    snapshotId: str
    scope: Scope
    project: dict[str, Any]


class ValidationRequest(_Snapshot, total=False):
    metamodels: dict[str, Any]
    ruleIds: list[RuleId]


class Location(TypedDict):
    path: str
    modelId: Optional[str]
    elementId: Optional[str]
    referenceId: Optional[str]


class Finding(TypedDict):
    ruleIds: list[RuleId]
    status: Status
    severity: Severity
    code: str
    description: str
    location: Location
    evidence: dict[str, Any]


class RuleResult(TypedDict):
    ruleId: RuleId
    status: Status
    severity: Severity
    description: str
    checkCounts: dict[Status, int]
    modelPaths: list[str]


class IncludedModel(TypedDict):
    modelId: Optional[str]
    path: str


class Coverage(TypedDict):
    requestedScope: Scope
    effectiveScope: Scope
    models: list[IncludedModel]
    incompletePaths: list[str]


class ValidationResult(TypedDict):
    schemaVersion: Literal[1]
    projectId: str
    snapshotId: str
    coverage: Coverage
    rules: list[RuleResult]
    findings: list[Finding]
