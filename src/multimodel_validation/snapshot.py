from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterator, Optional


MISSING = object()
RELATION_ENDPOINTS = ("sourceId", "targetId")


def identifier(value: Any) -> Optional[str]:
    """Entity IDs are opaque, nonblank strings. Preserve their exact spelling."""
    if isinstance(value, str) and value and value == value.strip() and all(
        char.isprintable() for char in value
    ):
        return value
    return None


@dataclass(frozen=True)
class Record:
    data: dict[str, Any]
    path: str

    @property
    def id(self) -> Optional[str]:
        return identifier(self.data.get("id"))

    def children(self, key: str, gaps: list[str]) -> Iterator["Record"]:
        path = f"{self.path}/{key}"
        values = self.data.get(key)
        if not isinstance(values, list):
            gaps.append(path)
            return
        for position, value in enumerate(values):
            child_path = f"{path}/{position}"
            if isinstance(value, dict):
                yield Record(value, child_path)
            else:
                gaps.append(child_path)


@dataclass(frozen=True)
class Reference:
    owner: Record
    field: str
    value: Any
    unsupported_collection: bool = False


def _origin_references(owner: Record, field: str) -> Iterator[tuple[str, Reference]]:
    if field not in owner.data:
        return
    path = f"{owner.path}/{field}"
    values = owner.data[field]
    if not isinstance(values, list):
        yield path, Reference(owner, field, values, unsupported_collection=True)
        return
    for position, value in enumerate(values):
        yield f"{path}/{position}", Reference(owner, field, value)


@dataclass(frozen=True)
class Model:
    record: Record
    elements: list[Record]
    relationships: dict[str, Record]
    elements_by_id: dict[str, list[Record]]
    element_gaps: list[str]
    relationship_gaps: list[str]

    @classmethod
    def from_record(cls, record: Record) -> "Model":
        element_gaps: list[str] = []
        relationship_gaps: list[str] = []
        elements = list(record.children("elements", element_gaps))
        relationships = {child.path: child for child in record.children("relationships", relationship_gaps)}
        by_id: dict[str, list[Record]] = defaultdict(list)
        for element in elements:
            if element.id is None:
                element_gaps.append(f"{element.path}/id")
            else:
                by_id[element.id].append(element)
        return cls(record, elements, relationships, by_id, element_gaps, relationship_gaps)

    def reference_index(self) -> dict[str, Reference]:
        references = dict(_origin_references(self.record, "sourceModelIds"))
        for element in self.elements:
            parent = element.data.get("parentId")
            if parent is not None:
                references[f"{element.path}/parentId"] = Reference(element, "parentId", parent)
            references.update(_origin_references(element, "sourceModelElements"))
        for path, relationship in self.relationships.items():
            for field in RELATION_ENDPOINTS:
                references[f"{path}/{field}"] = Reference(
                    relationship, field, relationship.data.get(field, MISSING)
                )
        return references


@dataclass(frozen=True)
class Snapshot:
    models: list[Model]
    models_by_id: dict[str, list[Model]]
    inventory_gaps: list[str]

    @classmethod
    def from_project(cls, project: dict[str, Any]) -> "Snapshot":
        inventory_gaps: list[str] = []
        models = [
            Model.from_record(record)
            for record in _model_records(Record(project, "/project"), inventory_gaps)
        ]
        by_id: dict[str, list[Model]] = defaultdict(list)
        for model in models:
            if model.record.id is None:
                inventory_gaps.append(f"{model.record.path}/id")
            else:
                by_id[model.record.id].append(model)
        return cls(models, by_id, inventory_gaps)

    @property
    def incomplete_paths(self) -> list[str]:
        paths = list(self.inventory_gaps)
        for model in self.models:
            paths.extend(model.element_gaps)
            paths.extend(model.relationship_gaps)
        return paths


def _model_records(project: Record, gaps: list[str]) -> Iterator[Record]:
    for line in project.children("productLines", gaps):
        for section in ("scope", "domainEngineering", "applicationEngineering"):
            path = f"{line.path}/{section}"
            value = line.data.get(section)
            if not isinstance(value, dict):
                gaps.append(path)
                continue

            engineering = Record(value, path)
            yield from engineering.children("models", gaps)
            if section == "applicationEngineering":
                for application in engineering.children("applications", gaps):
                    yield from application.children("models", gaps)
                    for adaptation in application.children("adaptations", gaps):
                        yield from adaptation.children("models", gaps)
