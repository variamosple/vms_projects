"""Read-only version evidence contracts; no persistent revision store is implied."""

from typing import Any, Literal, Optional, TypedDict

from .contracts import Coverage, Location, Scope, Severity, Status


VersionRuleId = Literal["RG17", "RG18", "RG19"]


class _VersionRequest(TypedDict):
    schemaVersion: Literal[1]
    projectId: str
    snapshotId: str
    scope: Scope
    project: dict[str, Any]


class VersionRequest(_VersionRequest, total=False):
    versionContext: dict[str, Any]


class VersionFinding(TypedDict):
    ruleIds: list[VersionRuleId]
    status: Status
    severity: Severity
    code: str
    description: str
    location: Location
    evidence: dict[str, Any]


class VersionRuleResult(TypedDict):
    ruleId: VersionRuleId
    status: Status
    severity: Severity
    description: str
    checkCounts: dict[Status, int]
    modelPaths: list[str]


class VersionResult(TypedDict):
    schemaVersion: Literal[1]
    kind: Literal["versions"]
    projectId: str
    snapshotId: str
    contextId: Optional[str]
    coverage: Coverage
    versionCoverage: dict[str, Any]
    rules: list[VersionRuleResult]
    findings: list[VersionFinding]
