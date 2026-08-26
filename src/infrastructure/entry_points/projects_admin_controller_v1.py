from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete
from sqlalchemy.orm import Session
from variamos_security import has_permissions, ResponseModel
from typing import Optional, Any
from datetime import datetime
import uuid

from src.db_connector import get_db
from src.model.modelDB import Project

class ProjectDTO(BaseModel):
    id: Optional[str] = None
    name: str
    description: Optional[str] = None
    project: Optional[Any] = None
    author: Optional[str] = None
    source: Optional[str] = None
    date: Optional[datetime] = None
    template: Optional[bool] = None
    isDeleted: Optional[bool] = Field(None, validation_alias="is_deleted")
    owners: Optional[list[Any]] = None

    model_config = {"from_attributes": True}


router = APIRouter(
    prefix="/v1/admin/projects",
    tags=["Projects", "Admin", "V1"],
)

@router.post("", dependencies=[Depends(has_permissions(["admin::projects::create"]))])
def create_project(request: Request, project: ProjectDTO, db: Session = Depends(get_db)):
    project_id = project.id or str(uuid.uuid4())
    owner_id = "default_owner"
    if hasattr(request.state, "user") and request.state.user:
        owner_id = request.state.user.id
        
    dbProject = Project(
        id=project_id,
        name=project.name,
        template=project.template if project.template is not None else False,
        author=project.author,
        description=project.description,
        source=project.source,
        owner_id=owner_id,
        project=project.project or {},
        date=datetime.utcnow()
    )
    db.add(dbProject)
    
    # Associate user to project via SQLAlchemy relationship
    from src.model.modelDB import User
    db_user = db.query(User).filter(User.id == owner_id).first()
    if db_user:
        dbProject.users.append(db_user)
        
    db.commit()
    db.refresh(dbProject)

    owners = [
        {
            "id": user.id,
            "name": user.name,
            "email": user.email,
        }
        for user in dbProject.users
    ]

    return ResponseModel(
        transactionId="ProjectsAdminCreate", 
        data=ProjectDTO(
            id=dbProject.id,
            name=dbProject.name,
            template=dbProject.template,
            author=dbProject.author,
            description=dbProject.description,
            source=dbProject.source,
            date=dbProject.date,
            is_deleted=dbProject.is_deleted,
            owners=owners
        )
    )

@router.get("", dependencies=[Depends(has_permissions(["admin::projects::query"]))])
def get_projects(
    name: Optional[str] = Query(None), 
    is_template: Optional[bool] = Query(None, alias="isTemplate"), 
    is_deleted: Optional[bool] = Query(None, alias="isDeleted"),
    include_deleted: bool = Query(False, alias="includeDeleted"),
    page_number: int = Query(1, alias="pageNumber"), 
    page_size: int = Query(20, alias="pageSize"),
    db: Session = Depends(get_db)
):
    query = db.query(Project)

    if is_deleted is not None:
        query = query.filter(Project.is_deleted == is_deleted)
    elif not include_deleted:
        query = query.filter(Project.is_deleted == False)

    if name is not None:
        query = query.filter(Project.name.ilike(f"%{name}%"))

    if is_template is not None:
        query = query.filter(Project.template == is_template)

    total = query.count()
    projects = query.offset((page_number - 1) * page_size).limit(page_size).all()

    data = []
    for dbProject in projects:
        owners = [
            {
                "id": user.id,
                "name": user.name,
                "email": user.email,
            }
            for user in dbProject.users
        ]
        data.append(
            ProjectDTO(
                id=dbProject.id,
                name=dbProject.name,
                description=dbProject.description,
                project=dbProject.project,
                author=dbProject.author,
                source=dbProject.source,
                date=dbProject.date,
                template=dbProject.template,
                is_deleted=dbProject.is_deleted,
                owners=owners
            )
        )

    return ResponseModel( transactionId="ProjectsAdminQuery", totalCount=total, data=data)

@router.put("/{project_id}", dependencies=[Depends(has_permissions(["admin::projects::update"]))])
def update_project(project_id: str, project: ProjectDTO, db: Session = Depends(get_db)):
    dbProject = db.query(Project).filter(Project.id == project_id).first()

    if not project:
        return ResponseModel( transactionId="ProjectsAdminUpdate", errorCode=404, message="Project not found")

    dbProject.name = project.name
    dbProject.template = project.template
    dbProject.author = project.author
    dbProject.description = project.description
    dbProject.source = project.source

    db.commit()
    db.refresh(dbProject)

    return ResponseModel( transactionId="ProjectsAdminUpdate", data=ProjectDTO.model_validate(dbProject))

@router.delete("/{project_id}", dependencies=[Depends(has_permissions(["admin::projects::delete"]))])
def delete_project(project_id: str, db: Session = Depends(get_db)):
    dbProject = db.query(Project).filter(Project.id == project_id).first()

    if not dbProject:
        return ResponseModel(transactionId="ProjectsAdminDelete", errorCode=404, message="Project not found")

    dbProject.is_deleted = True
    db.commit()

    return ResponseModel( transactionId="ProjectsAdminDelete")