"""Before/after contract, separate from the snapshot HTTP contract."""

from typing import Any, Literal, Optional, TypedDict

from .contracts import Coverage, Scope, Severity, Status, ValidationResult
from .correspondence_contracts import CorrespondenceResult
from .constraint_contracts import ConstraintResult
from .version_contracts import VersionResult
from .trace_contracts import TraceResult


TransitionRuleId = Literal["RG13", "RG14", "RG15", "RG16"]
EntityKind = Literal["model", "element", "relationship"]
Phase = Literal["before", "after"]


class _TransitionCapture(TypedDict):
    schemaVersion: Literal[1]
    projectId: str
    snapshotId: str
    scope: Scope
    project: dict[str, Any]


class TransitionCapture(_TransitionCapture, total=False):
    metamodels: dict[str, Any]


class _TransitionRequest(TypedDict):
    schemaVersion: Literal[1]
    before: TransitionCapture
    after: TransitionCapture


class TransitionRequest(_TransitionRequest, total=False):
    correspondenceProfile: dict[str, Any]
    constraintProfile: dict[str, Any]
    versionContext: dict[str, Any]
    attemptContext: dict[str, Any]
    traceContext: dict[str, Any]


class EntityAddress(TypedDict):
    kind: EntityKind
    modelId: str
    id: str


class Change(TypedDict):
    operation: Literal["created", "updated", "deleted"]
    entity: EntityAddress
    beforePath: Optional[str]
    afterPath: Optional[str]
    changedFields: list[str]
    uncertainFields: list[str]


class Impact(TypedDict):
    changeIndex: int
    phase: Phase
    dependent: EntityAddress
    target: EntityAddress
    field: str
    referencePath: str


class TransitionFinding(TypedDict):
    ruleIds: list[TransitionRuleId]
    status: Status
    severity: Severity
    code: str
    description: str
    phase: Phase
    path: str
    evidence: dict[str, Any]


class TransitionRuleResult(TypedDict):
    ruleId: TransitionRuleId
    status: Status
    severity: Severity
    description: str
    checkCounts: dict[Status, int]


class CorrespondenceReview(TypedDict):
    phase: Phase
    status: Status
    code: str
    dependent: EntityAddress
    target: EntityAddress
    field: str
    referencePath: str
    changeIndexes: list[int]


class StructuralDelta(TypedDict):
    introduced: int
    persisting: int
    resolved: int
    unclassified: int


class StructuralValidation(TypedDict):
    before: ValidationResult
    after: ValidationResult
    delta: StructuralDelta


class TransitionResult(TypedDict):
    schemaVersion: Literal[1]
    kind: Literal["transition"]
    projectId: str
    beforeSnapshotId: str
    afterSnapshotId: str
    coverage: dict[Phase, Coverage]
    impactDepth: Literal[1]
    supportedReferenceFields: list[str]
    changes: list[Change]
    impacts: list[Impact]
    correspondenceReviews: list[CorrespondenceReview]
    structuralValidation: StructuralValidation
    correspondenceValidation: CorrespondenceResult
    constraintValidation: ConstraintResult
    versionValidation: VersionResult
    traceabilityValidation: TraceResult
    rules: list[TransitionRuleResult]
    findings: list[TransitionFinding]
