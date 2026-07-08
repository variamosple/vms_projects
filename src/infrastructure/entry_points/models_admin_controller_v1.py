from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.orm import Session
from variamos_security import has_permissions, ResponseModel
from typing import Optional, Any
import logging
from src.db_connector import get_db
from src.model.modelDB import Model, Project

logger = logging.getLogger(__name__)
class ModelDTO(BaseModel):
    id: str
    projectId: str
    projectName: Optional[str] = None
    engineeringType: Optional[str] = None
    name: str
    type: Optional[str] = None
    description: Optional[str] = None
    author: Optional[str] = None
    source: Optional[str] = None
    owners: Optional[list[Any]] = None

    model_config = {"from_attributes": True}

router = APIRouter(
    prefix="/v1/admin/models",
    tags=["Models", "Admin", "V1"],
)

@router.get("", dependencies=[Depends(has_permissions(["admin::models::query"]))])
@router.get("", dependencies=[Depends(has_permissions(["admin::models::query"]))])
def get_models(
    name: Optional[str] = Query(None),
    engineering_type: Optional[str] = Query(None, alias="engineeringType"),
    page_number: int = Query(1, alias="pageNumber"),
    page_size: int = Query(20, alias="pageSize"),
    db: Session = Depends(get_db)
):
    query = (
        db.query(Model)
        .join(Project, Model.project_id == Project.id)
    )
    if engineering_type:
        query = query.filter(Model.engineering_type == engineering_type)
    if name:
        like = f"%{name}%"
        query = query.filter(
            (Model.name.ilike(like))
            | (Project.name.ilike(like))
        )
    count_result = query.count()
    offset = (page_number - 1) * page_size
    models_db = (
        query
        .offset(offset)
        .limit(page_size)
        .all()
    )
    models = []
    for db_model in models_db:
        owners = [
            {
                "id": user.id,
                "name": user.name,
                "email": user.email,
            }
            for user in db_model.project.users
        ]
        models.append(
            ModelDTO(
                id=db_model.id,
                projectId=db_model.project.id,
                projectName=db_model.project.name,
                engineeringType=db_model.engineering_type,
                name=db_model.name,
                type=db_model.type,
                description=db_model.description,
                author=db_model.author,
                source=db_model.source,
                owners=owners,
            )
        )
    return ResponseModel(transactionId="ModelsAdminQuery", totalCount=count_result, data=models)

@router.put("/{model_id}", dependencies=[Depends(has_permissions(["admin::models::update"]))])
def update_model(model_id: str, model: ModelDTO, db: Session = Depends(get_db)):

    db_model = (
        db.query(Model)
        .filter(Model.id == model_id)
        .first()
    )

    if not db_model:
        return ResponseModel(
            transactionId="ProjectsAdminUpdate",
            errorCode=404,
            message="Model not found"
        )

    db_model.name = model.name
    db_model.type = model.type
    db_model.author = model.author
    db_model.source = model.source
    db_model.description = model.description

    if db_model.model:
        db_model.model["name"] = model.name
        db_model.model["author"] = model.author
        db_model.model["source"] = model.source
        db_model.model["description"] = model.description

    flag_modified(db_model, "model")

    db.commit()

    return ResponseModel(
        transactionId="ProjectsAdminUpdate",
        data=model
    )

@router.delete("/{model_id}", dependencies=[Depends(has_permissions(["admin::models::delete"]))])
def delete_model():
    return {"message": "Model deleted"}