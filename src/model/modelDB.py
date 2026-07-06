from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Table,
    Column,
    ForeignKey,
    String,
    JSON,
    MetaData,
    DateTime,
    Integer,
)
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base

metadata = MetaData(schema="variamos")
Base = declarative_base(metadata=metadata)


user_project_association = Table(
    "user_project",
    metadata,
    Column("user_id", String, ForeignKey("variamos.user.id"), primary_key=True),
    Column("project_id", String, ForeignKey("variamos.project.id"), primary_key=True),
    Column("role", String, nullable=True),
)


class Project(Base):
    __tablename__ = 'project'
    __table_args__ = {'schema': 'variamos'}

    id = Column(String, primary_key=True)
    owner_id = Column(String, ForeignKey('variamos.user.id'), nullable=False)

    project = Column(JSON)

    name = Column(String)
    template = Column(Boolean)
    description = Column(String, nullable=True)
    source = Column(String, nullable=True)
    author = Column(String, nullable=True)
    date = Column(DateTime, nullable=True)

    type_models = Column(String, nullable=True)
    is_collaborative = Column(Boolean, default=True, nullable=False)

    owner = relationship("User", back_populates="owned_projects")

    users = relationship(
        "User",
        secondary=user_project_association,
        back_populates="projects"
    )

    history_records = relationship(
        "ProjectHistory",
        back_populates="project_ref",
        cascade="all, delete-orphan"
    )

    annotations = relationship(
        "ProjectAnnotation",
        back_populates="project_ref",
        cascade="all, delete-orphan"
    )

class Model(Base):
    __tablename__ = "model"
    __table_args__ = {"schema": "variamos"}

    id = Column(String, primary_key=True)

    project_id = Column(
        String,
        ForeignKey("variamos.project.id"),
        nullable=False
    )

    product_line_id = Column(String, nullable=False)
    engineering_type = Column(String, nullable=False)

    name = Column(String, nullable=True)
    type = Column(String, nullable=True)
    language_id = Column(
        Integer,
        ForeignKey("variamos.language.id"),
        nullable=False,
        index=True
    )
    description = Column(String, nullable=True)
    author = Column(String, nullable=True)
    source = Column(String, nullable=True)    
    model = Column(JSON, nullable=False)
    project = relationship("Project", back_populates="models")

    configurations = relationship(
        "ModelConfiguration",
        back_populates="model",
        cascade="all, delete-orphan"
    )

class ModelConfiguration(Base):
    __tablename__ = "model_configuration"
    __table_args__ = {"schema": "variamos"}

    id = Column(String, primary_key=True)

    model_id = Column(
        String,
        ForeignKey("variamos.model.id"),
        nullable=False,
        index=True
    )

    name = Column(String, nullable=False)

    configuration = Column(JSON, nullable=False)

    model = relationship(
        "Model",
        back_populates="configurations"
    )

class User(Base):
    __tablename__ = "user"
    __table_args__ = {"schema": "variamos"}

    id = Column(String, primary_key=True, default=lambda: str(uuid4()))
    user = Column(String)
    name = Column(String)
    email = Column(String)

    owned_projects = relationship(
        "Project",
        back_populates="owner",
    )

    projects = relationship(
        "Project",
        secondary=user_project_association,
        back_populates="users",
    )

    history_records = relationship(
        "ProjectHistory",
        back_populates="user_ref",
    )

    annotations = relationship(
        "ProjectAnnotation",
        back_populates="user_ref",
    )


class ProjectHistory(Base):
    __tablename__ = "project_history"
    __table_args__ = {"schema": "variamos"}

    id = Column(String, primary_key=True, default=lambda: str(uuid4()))

    project_id = Column(
        String,
        ForeignKey("variamos.project.id"),
        nullable=False,
    )

    model_id = Column(
        String,
        ForeignKey("variamos.model.id"),
        nullable=True,
    )

    user_id = Column(
        String,
        ForeignKey("variamos.user.id"),
        nullable=True,
    )

    action_type = Column(String, nullable=False)
    entity_type = Column(String, nullable=False)
    entity_id = Column(String, nullable=True)
    entity_name = Column(String, nullable=True)
    old_value = Column(JSON, nullable=True)
    new_value = Column(JSON, nullable=True)
    description = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False)

    project_ref = relationship(
        "Project",
        back_populates="history_records",
    )

    model_ref = relationship(
        "Model",
        back_populates="history_records",
    )

    user_ref = relationship(
        "User",
        back_populates="history_records",
    )


class ProjectAnnotation(Base):
    __tablename__ = "project_annotation"
    __table_args__ = {"schema": "variamos"}

    id = Column(String, primary_key=True, default=lambda: str(uuid4()))

    project_id = Column(
        String,
        ForeignKey("variamos.project.id"),
        nullable=False,
    )

    model_id = Column(
        String,
        ForeignKey("variamos.model.id"),
        nullable=False,
    )

    user_id = Column(
        String,
        ForeignKey("variamos.user.id"),
        nullable=True,
    )

    annotation = Column(JSON, nullable=False)

    is_resolved = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)

    project_ref = relationship(
        "Project",
        back_populates="annotations",
    )

    model_ref = relationship(
        "Model",
        back_populates="annotations",
    )

    user_ref = relationship(
        "User",
        back_populates="annotations",
    )