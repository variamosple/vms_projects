from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.orm import Session
from variamos_security import has_permissions, ResponseModel
from typing import Optional, Any
import logging
import uuid
from src.db_connector import get_db
from src.model.modelDB import Model, Project

logger = logging.getLogger(__name__)
class ModelDTO(BaseModel):
    id: Optional[str] = None
    projectId: str
    projectName: Optional[str] = None
    engineeringType: Optional[str] = None
    name: str
    type: Optional[str] = None
    description: Optional[str] = None
    author: Optional[str] = None
    source: Optional[str] = None
    owners: Optional[list[Any]] = None
    isDeleted: Optional[bool] = None
    modelLevel: Optional[str] = None
    isPublic: Optional[bool] = None
    languageId: Optional[int] = None

    model_config = {"from_attributes": True}


router = APIRouter(
    prefix="/v1/admin/models",
    tags=["Models", "Admin", "V1"],
)

@router.post("", dependencies=[Depends(has_permissions(["admin::models::create"]))])
def create_model(model: ModelDTO, db: Session = Depends(get_db)):
    model_id = model.id or str(uuid.uuid4())
    db_model = Model(
        id=model_id,
        project_id=model.projectId,
        product_line_id=model.projectId, # default product line to project id
        engineering_type=model.engineeringType or "scope",
        name=model.name,
        type=model.type,
        language_id=model.languageId if model.languageId is not None else 1, # default to 1 if none
        description=model.description,
        author=model.author,
        source=model.source,
        model={}, # default empty model
        is_deleted=False,
        model_level=model.modelLevel or "domain",
        is_public=model.isPublic if model.isPublic is not None else True,
    )
    db.add(db_model)
    db.commit()
    db.refresh(db_model)
    
    # Return updated model with ID
    model.id = model_id
    return ResponseModel(transactionId="ModelsAdminCreate", data=model)

@router.get("", dependencies=[Depends(has_permissions(["admin::models::query"]))])
def get_models(
    name: Optional[str] = Query(None),
    engineering_type: Optional[str] = Query(None, alias="engineeringType"),
    model_level: Optional[str] = Query(None, alias="modelLevel"),
    is_deleted: Optional[bool] = Query(None, alias="isDeleted"),
    is_public: Optional[bool] = Query(None, alias="isPublic"),
    include_deleted: bool = Query(False, alias="includeDeleted"),
    page_number: int = Query(1, alias="pageNumber"),
    page_size: int = Query(20, alias="pageSize"),
    db: Session = Depends(get_db)
):
    query = (
        db.query(Model)
        .join(Project, Model.project_id == Project.id)
    )
    if is_deleted is not None:
        query = query.filter(Model.is_deleted == is_deleted)
    elif not include_deleted:
        query = query.filter(Model.is_deleted == False)
        
    if is_public is not None:
        query = query.filter(Model.is_public == is_public)

    if model_level:
        query = query.filter(Model.model_level == model_level)
        
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
                isDeleted=db_model.is_deleted,
                modelLevel=db_model.model_level,
                isPublic=db_model.is_public,
                languageId=db_model.language_id,
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
    db_model.model_level = model.modelLevel
    db_model.is_public = model.isPublic
    if model.languageId is not None:
        db_model.language_id = model.languageId

    if db_model.model:
        db_model.model["name"] = model.name
        db_model.model["author"] = model.author
        db_model.model["source"] = model.source
        db_model.model["description"] = model.description
        db_model.model["modelLevel"] = model.modelLevel
        db_model.model["isPublic"] = model.isPublic

    flag_modified(db_model, "model")

    db.commit()

    return ResponseModel(
        transactionId="ProjectsAdminUpdate",
        data=model
    )

@router.delete("/{model_id}", dependencies=[Depends(has_permissions(["admin::models::delete"]))])
def delete_model(model_id: str, db: Session = Depends(get_db)):
    db_model = db.query(Model).filter(Model.id == model_id).first()

    if not db_model:
        return ResponseModel(transactionId="ModelsAdminDelete", errorCode=404, message="Model not found")

    db_model.is_deleted = True
    db.commit()

    return ResponseModel(transactionId="ModelsAdminDelete")