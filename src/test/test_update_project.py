"""Saving an existing project (POST /saveProject with an id).

The tests run on an in-memory SQLite database. The foreign key from
model_configuration to model is declared ON DELETE CASCADE, as in the
production database, so deleting a model also deletes its configurations.
"""
import json
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.model.modelDAO import ProjectDao
from src.model.modelDB import (
    Base,
    Language,
    Model,
    ModelConfiguration,
    Project,
    User,
)

USER_ID = "user-1"


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def attach_schema(dbapi_connection, _record):
        dbapi_connection.execute("ATTACH DATABASE ':memory:' AS variamos")
        dbapi_connection.execute("PRAGMA foreign_keys = ON")

    tables = [
        Base.metadata.tables[f"variamos.{name}"]
        for name in ("user", "language", "project", "user_project", "model")
    ]
    Base.metadata.create_all(engine, tables=tables)
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE variamos.model_configuration ("
            " id VARCHAR PRIMARY KEY,"
            " model_id VARCHAR NOT NULL REFERENCES model (id) ON DELETE CASCADE,"
            " name VARCHAR NOT NULL,"
            " configuration JSON NOT NULL)"
        ))

    factory = sessionmaker(bind=engine, autoflush=False)
    with factory() as db:
        db.add(User(id=USER_ID, user="kit", name="Kit", email="kit@example.org"))
        db.add(Language(id=1, name="Feature model"))
        db.commit()
    yield factory
    engine.dispose()


def feature_model(model_id, name):
    return {
        "id": model_id,
        "languageId": "1",
        "name": name,
        "type": "Feature model",
        "elements": [],
        "relationships": [],
    }


def project_json(*models):
    return {
        "productLines": [{
            "id": "pl-1",
            "name": "PL",
            "domainEngineering": {"models": list(models)},
            "applicationEngineering": {"models": []},
            "scope": {"models": []},
        }]
    }


def save_request(project_id, *models, **fields):
    request = {
        "id": project_id,
        "name": "Project",
        "template": False,
        "description": "Description",
        "author": "Author",
        "source": "Source",
        "project": project_json(*models),
    }
    request.update(fields)
    return request


def create_project(session_factory, *models):
    with session_factory() as db:
        response = ProjectDao(db).create_project(save_request(None, *models), USER_ID)
    return json.loads(response.body)["data"]["id"]


def add_configuration(session_factory, model_id, name):
    with session_factory() as db:
        db.add(ModelConfiguration(
            id=str(uuid4()),
            model_id=model_id,
            name=name,
            configuration={"features": []},
        ))
        db.commit()


def update_project(session_factory, request):
    with session_factory() as db:
        return ProjectDao(db).update_project(request, USER_ID)


def configuration_count(session_factory, model_id):
    with session_factory() as db:
        return db.query(ModelConfiguration).filter(
            ModelConfiguration.model_id == model_id
        ).count()


def test_saving_an_unchanged_project_keeps_its_configurations(session_factory):
    m1 = feature_model(str(uuid4()), "M1")
    m2 = feature_model(str(uuid4()), "M2")
    project_id = create_project(session_factory, m1, m2)
    add_configuration(session_factory, m1["id"], "conf-a")
    add_configuration(session_factory, m1["id"], "conf-b")
    add_configuration(session_factory, m2["id"], "conf-c")

    response = update_project(session_factory, save_request(project_id, m1, m2))

    assert response.status_code == 200
    assert configuration_count(session_factory, m1["id"]) == 2
    assert configuration_count(session_factory, m2["id"]) == 1


def test_saving_updates_the_models_in_place(session_factory):
    m1 = feature_model(str(uuid4()), "M1")
    project_id = create_project(session_factory, m1)
    edited = dict(m1, name="M1 renamed", elements=[{"id": "e1", "properties": []}])

    update_project(session_factory, save_request(project_id, edited))

    with session_factory() as db:
        stored = db.get(Model, m1["id"])
        assert stored.name == "M1 renamed"
        assert stored.model["elements"] == [{"id": "e1", "properties": []}]


def test_saving_keeps_the_model_settings_made_by_the_administration(session_factory):
    m1 = feature_model(str(uuid4()), "M1")
    project_id = create_project(session_factory, m1)
    with session_factory() as db:
        stored = db.get(Model, m1["id"])
        stored.is_public = False
        stored.model_level = "application"
        stored.description = "Set by the administration"
        db.commit()

    update_project(session_factory, save_request(project_id, m1))

    with session_factory() as db:
        stored = db.get(Model, m1["id"])
        assert stored.is_public is False
        assert stored.model_level == "application"
        assert stored.description == "Set by the administration"


def test_saving_deletes_the_removed_models_and_inserts_the_new_ones(session_factory):
    kept = feature_model(str(uuid4()), "Kept")
    removed = feature_model(str(uuid4()), "Removed")
    project_id = create_project(session_factory, kept, removed)
    add_configuration(session_factory, kept["id"], "conf-kept")
    add_configuration(session_factory, removed["id"], "conf-removed")
    added = feature_model(str(uuid4()), "Added")

    update_project(session_factory, save_request(project_id, kept, added))

    with session_factory() as db:
        ids = {model.id for model in db.query(Model).filter(Model.project_id == project_id)}
    assert ids == {kept["id"], added["id"]}
    assert configuration_count(session_factory, kept["id"]) == 1
    assert configuration_count(session_factory, removed["id"]) == 0


def test_saving_keeps_the_fields_missing_from_the_request(session_factory):
    m1 = feature_model(str(uuid4()), "M1")
    project_id = create_project(session_factory, m1)
    request = save_request(project_id, m1, name="Renamed")
    del request["description"]
    del request["author"]

    update_project(session_factory, request)

    with session_factory() as db:
        stored = db.get(Project, project_id)
        assert stored.name == "Renamed"
        assert stored.description == "Description"
        assert stored.author == "Author"


def test_saving_without_a_project_body_keeps_the_models(session_factory):
    m1 = feature_model(str(uuid4()), "M1")
    project_id = create_project(session_factory, m1)
    add_configuration(session_factory, m1["id"], "conf-a")

    update_project(session_factory, {"id": project_id, "name": "Renamed"})

    with session_factory() as db:
        assert db.get(Model, m1["id"]) is not None
    assert configuration_count(session_factory, m1["id"]) == 1


def test_saving_a_deleted_project_answers_404_and_writes_nothing(session_factory):
    m1 = feature_model(str(uuid4()), "M1")
    project_id = create_project(session_factory, m1)
    with session_factory() as db:
        db.query(Model).filter(Model.project_id == project_id).delete()
        db.execute(text("DELETE FROM variamos.user_project"))
        db.query(Project).filter(Project.id == project_id).delete()
        db.commit()

    with session_factory() as db:
        with pytest.raises(HTTPException) as error:
            ProjectDao(db).update_project(save_request(project_id, m1), USER_ID)

        assert error.value.status_code == 404
        # The same session still answers after the refusal.
        assert db.query(Model).count() == 0
        assert db.query(Project).count() == 0
