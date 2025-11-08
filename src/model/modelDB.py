from uuid import uuid4

from sqlalchemy import Boolean, Table, Column, ForeignKey, Integer, String, JSON, MetaData
from sqlalchemy import DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base

metadata = MetaData(schema="variamos")
Base = declarative_base(metadata=metadata)


user_project_association = Table(
    'user_project', metadata,
    Column('user_id', String, ForeignKey('variamos.user.id'), primary_key=True),
    Column('project_id', String, ForeignKey('variamos.project.id'), primary_key=True),
    Column('role', String, nullable=True)  # Rol del usuario en el proyecto
)

# Tabla de asociación
class Project(Base):
    __tablename__ = 'project'
    __table_args__ = {'schema': 'variamos'}

    id = Column(String, primary_key=True)
    owner_id = Column(String, ForeignKey('variamos.user.id'), nullable=False)
    project = Column(JSON)
    name = Column(String)
    template = Column(Boolean)
    configuration = Column(JSON,  nullable=True)
    description = Column(String,  nullable=True)
    source = Column(String,  nullable=True)
    author = Column(String,  nullable=True)
    date = Column(DateTime,  nullable=True)
    type_models = Column(String, nullable=True)
    is_collaborative = Column(Boolean, default=True, nullable=False)

    owner = relationship("User", back_populates="owned_projects")

    users = relationship(
        "User",
        secondary= user_project_association,
        back_populates="projects"
    )


class User(Base):
    __tablename__ = 'user'
    __table_args__ = {'schema': 'variamos'}

    id = Column(String, primary_key=True, default=str(uuid4()))
    user = Column(String)
    name = Column(String)
    email = Column(String)

    owned_projects = relationship(
        "Project",
        back_populates="owner"
    )
    projects = relationship(
        "Project",
        secondary=user_project_association,
        back_populates="users"
    )
