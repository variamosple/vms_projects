from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import DateTime, select, and_, exists, cast, String
from sqlalchemy.sql import func
from datetime import datetime
from .modelDB import Model, User, Project, user_project_association, ProjectHistory, ProjectAnnotation, ModelConfiguration
from fastapi.responses import JSONResponse
from ..utils.configurationManager import manage_configurations
from copy import deepcopy
from collections import defaultdict

class UserDao:
    def __init__(self, db: Session):
        self.db = db

    def get_projects(self, user_id: str):
        # Primero, busquemos todos los project_ids asociados al user_id
        stmt = select(user_project_association.c.project_id, user_project_association.c.role).where(user_project_association.c.user_id == user_id)
        result = self.db.execute(stmt).fetchall()

        project_roles = [{"project_id": row.project_id, "role": row.role} for row in result]
        project_ids = [row["project_id"] for row in project_roles]
    
        projects = self.db.query(Project).filter(Project.id.in_(project_ids)).all()
    
        records = []
        for project in projects:
            role = next((pr["role"] for pr in project_roles if pr["project_id"] == project.id), None)
            records.append({"id":  project.id, "owner_id": project.owner_id , "name": project.name, "template": project.template, "description": project.description, "source": project.source, "author": project.author, "date": project.date, "role":role, "is_collaborative": project.is_collaborative})
        
        self.db.close()
        return {"transactionId": "1", "message": "Ok", "data": { "projects": records}}

    def get_by_username(self, username: str):
        user = self.db.query(User).filter(User.user == username).first()
        self.db.close()
        return user

    def get_by_email(self, email: str):
        user = self.db.query(User).filter(User.email == email).first()
        self.db.close()
        return user

class ProjectDao:
    def __init__(self, db: Session):
        self.db = db

    def _extract_models(self, project_json: dict):
        project = deepcopy(project_json)
        models = []

        for product_line in project.get("productLines", []):
            product_line_id = product_line["id"]

            for engineering_type in (
                "domainEngineering",
                "applicationEngineering",
                "scope",
            ):
                engineering = product_line.get(engineering_type)

                if not engineering:
                    continue

                extracted_models = []

                for model in engineering.get("models", []):
                    extracted_models.append({
                        "id": model["id"],
                        "project_id": None,
                        "product_line_id": product_line_id,
                        "engineering_type": engineering_type,
                        "language_id": model["languageId"],
                        "name": model.get("name"),
                        "type": model.get("type"),
                        "description": model.get("description"),
                        "author": model.get("author"),
                        "source": model.get("source"),
                        "model": model,
                    })

                models.extend(extracted_models)
                engineering["models"] = []

        return project, models
    
        
    def _rebuild_project(self, project_json, models):
        project = deepcopy(project_json)

        index = defaultdict(list)

        for model in models:
            model_json = deepcopy(model.model)

            model_json["id"] = model.id
            model_json["projectId"] = model.project_id
            model_json["productLineId"] = model.product_line_id
            model_json["engineeringType"] = model.engineering_type
            model_json["name"] = model.name
            model_json["type"] = model.type
            model_json["description"] = model.description
            model_json["author"] = model.author
            model_json["source"] = model.source
            model_json["languageId"] = model.language_id

            index[(model.product_line_id, model.engineering_type)].append(model_json)

        for product_line in project.get("productLines", []):
            for engineering_type in (
                "domainEngineering",
                "applicationEngineering",
                "scope",
            ):
                engineering = product_line.get(engineering_type)

                if engineering:
                    engineering["models"] = index.get(
                        (product_line["id"], engineering_type),
                        [],
                    )

        return project

    def _extract_configurations(self, configuration_json):
        configurations = []

        if not configuration_json:
            return configurations

        model_configs = configuration_json.get("modelConfigurations",{})
        for model_id, configs in model_configs.items():
            for config in configs:
                configurations.append({
                    "id": config["id"],
                    "model_id": model_id,
                    "name": config["name"],
                    "configuration": config,
                })
        return configurations

    def _rebuild_configurations(self, configurations):
        model_configs = defaultdict(list)

        for config in configurations:
            configuration = deepcopy(config.configuration)
            configuration["id"] = config.id
            configuration["name"] = config.name
            model_configs[config.model_id].append(configuration)

        return {"modelConfigurations": dict(model_configs)}
    
    def _save_models(self, project_id: str, models: list):
        for model in models:
            self.db.add(
                Model(
                    id=model["id"],
                    project_id=project_id,
                    product_line_id=model["product_line_id"],
                    engineering_type=model["engineering_type"],
                    language_id=model["language_id"],
                    name=model["name"],
                    type=model["type"],
                    model=model["model"],
                )
            )

    

    def get_by_id(self, user_id: str, project_id: str):
        project = (
            self.db.query(Project)
            .options(
                selectinload(Project.models).selectinload(Model.configurations)
            )
            .filter(Project.id == project_id)
            .first()
        )

        if not project:
            self.db.close()
            raise HTTPException(status_code=404, detail="Project not found")

        # Force le chargement des relations avant qu'une autre méthode ferme la session
        models = list(project.models)
        for model in models:
            _ = list(model.configurations)

        role = None
        collaborators = []

        if not project.template:
            try:
                role_data = self.get_user_role(project_id, user_id)
                role = role_data["data"]["role"]
            except HTTPException as e:
                if e.status_code != 404:
                    raise e

            try:
                collaborators_data = self.get_users(project_id, user_id)
                collaborators = collaborators_data["data"]["users"]
            except Exception:
                collaborators = []

        all_configurations = []

        for model in project.models:
            all_configurations.extend(model.configurations)

        project_data = {
            "id": project.id,
            "name": project.name,
            "owner_id": project.owner_id,
            "project": self._rebuild_project(project.project, project.models),
            "template": project.template,
            "description": project.description,
            "source": project.source,
            "author": project.author,
            "date": project.date,
            "configuration": self._rebuild_configurations(all_configurations),
            "is_collaborative": project.is_collaborative,
            "role": role,
            "collaborators": collaborators,
        }

        try:
            self.db.close()
        except Exception:
            pass

        return {
            "transactionId": "1",
            "message": "Ok",
            "data": {
                "project": project_data
            }
        }

    def apply_configuration(self, project_id: str, model_id: str, configuration_id: str):
        project = self.db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        model = (
            self.db.query(Model)
            .filter(
                Model.project_id == project_id,
                Model.id == model_id
            )
            .first()
        )
        if not model:
            raise HTTPException(status_code=404, detail="Model not found")
        configuration = (
            self.db.query(ModelConfiguration)
            .filter(
                ModelConfiguration.id == configuration_id,
                ModelConfiguration.model_id == model_id
            )
            .first()
        )
        if not configuration:
            raise HTTPException(status_code=404, detail="Configuration not found")
        try:
            model_json = deepcopy(model.model)
            feature_values = {}
            for feature in configuration.configuration["features"]:
                for prop in feature.get("properties", []):
                    if "id" in prop and "value" in prop:
                        feature_values[prop["id"]] = prop["value"]

            for element in model_json.get("elements", []):
                for prop in element.get("properties", []):
                    if prop["id"] in feature_values:
                        prop["value"] = feature_values[prop["id"]]

            updated_model = deepcopy(model)
            updated_model.model = model_json
            project_json = self._rebuild_project( project.project, project.models)
            for product_line in project_json.get("productLines", []):
                if product_line["id"] != model.product_line_id:
                    continue
                engineering = product_line.get(model.engineering_type)
                if not engineering:
                    break
                for i, current_model in enumerate(engineering.get("models", [])):
                    if current_model["id"] == model.id:
                        engineering["models"][i] = model_json
                        break
                break
            return {"transactionId": "1", "message": "Configuration applied successfully", "data": project_json, }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    def get_model_configurations(self, project_id: str, model_id: str):
        model = (
            self.db.query(Model)
            .filter(
                Model.project_id == project_id,
                Model.id == model_id
            )
            .first()
        )

        if not model:
            raise HTTPException(status_code=404, detail="Model not found")

        configurations = self._rebuild_configurations(model.configurations)
        model_configs = configurations.get("modelConfigurations", {}).get(model_id, [])

        if not model_configs:
            return {"transactionId": "1", "message": "No configurations available for the specified model", "data": []}
        return {"transactionId": "1", "message": "Configurations retrieved successfully", "data": model_configs}

    def get_template_projects(self):
        projects = self.db.query(Project).filter(Project.template == True).all()
        self.db.close()
        records = []
        for project in projects:
            records.append({"id":  project.id, "name": project.name, "template": project.template, "description": project.description, "source": project.source, "author": project.author, "date": project.date})
        return {"transactionId": "1", "message": "Ok", "data": { "projects": records}}

    def create_project(self, project_dict: dict, user_id: str):
        print("creando proyecto...")
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
            self.db.close()
            raise Exception("El usuario no existe")

        is_template_flag = project_dict.get("template", False)
        is_collab = False if is_template_flag else True

        project_json, extracted_models = self._extract_models(
            project_dict.get("project")
        )

        project = Project(
            id=str(uuid4()),
            owner_id=user_id,
            name=project_dict.get("name"),
            description=project_dict.get("description"),
            author=project_dict.get("author"),
            source=project_dict.get("source"),
            date=datetime.now(),
            project=project_json,
            template=is_template_flag,
            is_collaborative=is_collab,
        )

        self.db.add(project)
        self.db.flush() 

        self._save_models(project.id, extracted_models)

        assoc = user_project_association.insert().values(
            user_id=user_id,
            project_id=project.id,
            role="owner"
        )
        self.db.execute(assoc)
        self.db.commit()
        print("proyecto creado")

        content = { "transactionId": "1", "message": "Project created successfully", "data": { "id": project.id } }
        self.db.close()
        return JSONResponse(content=content, status_code=200)

    def update_project(self, project_dict: dict, user_id: str):
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
            self.db.close()
            raise Exception("El usuario no existe")

        project_id = project_dict.get("id")
        project_json, extracted_models = self._extract_models(
            project_dict.get("project")
        )

        self.db.query(Project).filter(Project.id == project_id).update({
            "name": project_dict.get("name"),
            "project": project_json,
            "template": project_dict.get("template"),
            "description": project_dict.get("description"),
            "author": project_dict.get("author"),
            "source": project_dict.get("source"),
            "date": datetime.now(),
        })

        self.db.query(Model).filter(
            Model.project_id == project_id
        ).delete()

        self._save_models(project_id, extracted_models)
        self.db.commit()

        content = { "transactionId": "1", "message": "Project updated successfully", "data": {"id": project_id} }
        self.db.close()
        return JSONResponse(content=content, status_code=200)

    def update_project_name(self, project_dict: dict):
        id=project_dict.get("id")
        project=Project(id=id, name=project_dict.get("name"), project=project_dict.get("project"), template=project_dict.get("template"))
        self.db.query(Project).filter(Project.id == project.id).update({"name": project.name})
        self.db.commit()
        self.db.close()
        content = {"transactionId": "1", "message": "Project name updated successfully"}
        return JSONResponse(content=content, status_code=200)

    def add_configuration(self, project_id: str, project_json: dict, id_feature_model, config_name: str):
        project = (
            self.db.query(Project)
            .options(
                selectinload(Project.models).selectinload(Model.configurations)
            )
            .filter(Project.id == project_id)
            .first()
        )        
        if not project:
            self.db.close()
            raise HTTPException(status_code=404, detail="Project not found")
        all_configurations = []
        for model in project.models:
            all_configurations.extend(model.configurations)

        configuration_json = self._rebuild_configurations(all_configurations)
        configuration_json = manage_configurations(
            project_json,
            id_feature_model,
            config_name,
            configuration_json
        )
        extracted_configs = self._extract_configurations(configuration_json)
        self.db.query(ModelConfiguration).filter(
            ModelConfiguration.model_id == id_feature_model
        ).delete()
        for config in extracted_configs:
            if config["model_id"] != id_feature_model:
                continue
            self.db.add(
                ModelConfiguration(
                    id=config["id"],
                    model_id=config["model_id"],
                    name=config["name"],
                    configuration=config["configuration"],
                )
            )
        self.db.commit()
        content = { "transactionId": "1", "message": "Project configuration updated successfully" }
        self.db.close()
        return JSONResponse(content=content, status_code=200)

    
    def delete_configuration_from_project(self, project_id: str, model_id: str, configuration_id: str):
        model = (
            self.db.query(Model)
            .filter(
                Model.project_id == project_id,
                Model.id == model_id
            )
            .first()
        )
        if not model:
            raise HTTPException(status_code=404, detail="Model not found")
        configuration = (
            self.db.query(ModelConfiguration)
            .filter(
                ModelConfiguration.id == configuration_id,
                ModelConfiguration.model_id == model_id
            )
            .first()
        )
        if not configuration:
            raise HTTPException(status_code=404, detail="Configuration not found")
        self.db.delete(configuration)
        self.db.commit()
        content = { "transactionId": "1", "message": "Configuration deleted successfully" }
        self.db.close()
        return JSONResponse(content=content, status_code=200)
    
    def delete_project(self, project_dict: dict):
        project_id = project_dict.get("id")
        project = self.db.query(Project).filter(Project.id == project_id).first()
        if not project:
            self.db.close()
            raise Exception("Project not found")
        self.db.execute(
            user_project_association.delete().where(
                user_project_association.c.project_id == project_id
            )
        )
        self.db.delete(project)
        self.db.commit()
        content = { "transactionId": "1", "message": "Project deleted successfully", "data": {"id": project_id} }
        self.db.close()
        return JSONResponse(content=content, status_code=200)
    
    def share_project(self, project_id: str, to_username_id: str, role: str):
        user = self.db.query(User).filter(User.id == to_username_id).first()
        if not user:
            self.db.close()
            raise Exception("El usuario no existe")
        # Verificar ya está compartido con el usuario
        assoc_exists = self.db.execute(select(user_project_association)
                                       .where(and_(user_project_association.c.user_id == user.id,
                                                   user_project_association.c.project_id == project_id))).fetchone()
        if not assoc_exists:
            assoc = user_project_association.insert().values(user_id=user.id, project_id=project_id, role=role)
            self.db.execute(assoc)
        else:
            self.db.execute(user_project_association.update()
            .where(
                and_(
                    user_project_association.c.user_id == user.id,
                    user_project_association.c.project_id == project_id
                    )
                )
                .values(role=role)
            )

        self.db.commit()

        user_info = {
            "id": user.id,
            "username": user.user,
            "name": user.name,
            "email": user.email,
            "role": role
        }

        self.db.close()
        return JSONResponse(content={"message": "Project shared successfully", "data": user_info},
                            status_code=200)

    def get_users(self, project_id: str, requesting_user_id: str):
        is_user_associated = self.db.query(user_project_association).filter(
            and_(
                user_project_association.c.project_id == project_id,
                user_project_association.c.user_id == requesting_user_id
            )
        ).first()
        if not is_user_associated:
            self.db.close()
            raise Exception("El usuario no tiene permiso para ver los usuarios de este proyecto.")
        stmt = select(user_project_association.c.user_id, user_project_association.c.role).where(user_project_association.c.project_id == project_id)
        result = self.db.execute(stmt).fetchall()

        user_roles = {row.user_id: row.role for row in result}

        user_ids = list(user_roles.keys())
        users = self.db.query(User).filter(User.id.in_(user_ids)).all()
        users_list = [{"id": user.id, "username": user.user, "name": user.name, "email": user.email, "role": user_roles.get(user.id)} for user in users]
        self.db.close()
        return {"data": {"users": users_list}}

    def delete_collaborator(self, project_id: str, user_id: str, collaborator_id: str):
        project = self.db.query(Project).filter(Project.id == project_id).first()
        if not project:
            self.db.close()
            raise Exception("El proyecto no existe")
        if project.owner_id != user_id:
            self.db.close()
            raise Exception("El usuario no es el propietario del proyecto")
        
        assoc_exists = self.db.query(user_project_association).filter(
            and_(
                user_project_association.c.user_id == collaborator_id,
                user_project_association.c.project_id == project_id
            )
        ).first()
        if not assoc_exists:
            self.db.close()
            raise Exception("El colaborador no existe en el proyecto")
        
        self.db.execute(user_project_association.delete().where(
            and_(
                user_project_association.c.user_id == collaborator_id,
                user_project_association.c.project_id == project_id
            )
        ))
        self.db.commit()
        self.db.close()
        return JSONResponse(content={"message": "Collaborator deleted successfully"},
                            status_code=200)

    def change_user_role(self, project_id: str, user_id: str, collaborator_id: str, role: str):
        project = self.db.query(Project).filter(Project.id == project_id).first()
        if not project:
            self.db.close()
            raise Exception("El proyecto no existe")
        if project.owner_id != user_id:
            self.db.close()
            raise Exception("El usuario no es el propietario del proyecto")
        
        assoc_exists = self.db.query(user_project_association).filter(
            and_(
                user_project_association.c.user_id == collaborator_id,
                user_project_association.c.project_id == project_id
            )
        ).first()
        if not assoc_exists:
            self.db.close()
            raise Exception("El colaborador no existe en el proyecto")
        
        self.db.execute(user_project_association.update().where(
            and_(
                user_project_association.c.user_id == collaborator_id,
                user_project_association.c.project_id == project_id
            )
        ).values(role=role))
        self.db.commit()
        self.db.close()
        return JSONResponse(content={"message": "Collaborator role changed successfully"},
                            status_code=200)
        
    def change_project_collaborative(self, project_id: str , user_id:str):
        project = self.db.query(Project).filter(Project.id == project_id).first()
        if not project:
            self.db.close()
            raise Exception("El proyecto no existe")
        if project.owner_id != user_id:
            self.db.close()
            raise Exception("El usuario no es el propietario del proyecto")
        project.is_collaborative = not project.is_collaborative
        self.db.commit()
        collaborative_state = project.is_collaborative
        self.db.close()
        return JSONResponse(content={"message": "Project collaborative status changed successfully", "data": {"collaborativeState": collaborative_state}},
                            status_code=200)
    
    def get_user_role(self, project_id: str, user_id: str):
        stmt = select(user_project_association.c.role).where(
            and_(
                user_project_association.c.project_id == project_id,
                user_project_association.c.user_id == user_id
            )
        )
        result = self.db.execute(stmt).fetchone()
        if result:
            role = result.role
            return {"message": "Role retrieved successfully", "data": {"role": role}}
        else:
            raise HTTPException(status_code=404, detail="Role not found")
        
    def create_history(self, history_data: dict, user_id: str):
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
           self.db.close()
           raise Exception("El usuario no existe")
        history = ProjectHistory(id=str(uuid4()), project_id=history_data.get("projectId"), model_id=history_data.get("modelId"), user_id=user.id, action_type=history_data.get("actionType"), entity_type=history_data.get("entityType"), entity_id=history_data.get("entityId"), entity_name=history_data.get("entityName"), old_value=history_data.get("oldValue"), new_value=history_data.get("newValue"), 
                                 description=history_data.get("description"), created_at=datetime.now())
        self.db.add(history)
        self.db.commit()
        content = {"transactionId": "1", "message": "History event registered successfully", "data": {"id": str(history.id), "userName": user.name, "createdAt": history.created_at.isoformat() if history.created_at else None}}
        self.db.close()
        return JSONResponse(content=content, status_code=200)
    
    def get_history(self, project_id: str):
        records = (self.db.query(ProjectHistory).filter(ProjectHistory.project_id == project_id).order_by(ProjectHistory.created_at.desc()).all())
        records_list = []
        for history in records:
            records_list.append({"id": str(history.id), "projectId": history.project_id, "modelId": history.model_id, "userId": history.user_id, "userName": history.user_ref.name if history.user_ref else None, "actionType": history.action_type, "entityType": history.entity_type, "entityId": history.entity_id, "entityName": history.entity_name, "oldValue": history.old_value, "newValue": history.new_value, "description": history.description, "createdAt": history.created_at.isoformat() if history.created_at else None})
        content = {"transactionId": "1", "message": "History retrieved successfully", "data": records_list}
        self.db.close()
        return JSONResponse(content=content, status_code=200)
    
    def create_annotation(self, annotation_data: dict, user_id: str):
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:  
            self.db.close()
            raise Exception("El usuario no existe")
        annotation = ProjectAnnotation(id=str(uuid4()), project_id=annotation_data.get("projectId"), model_id=annotation_data.get("modelId"), user_id=user.id, 
                                       annotation=annotation_data.get("annotation"), is_resolved=False, created_at=datetime.now(), updated_at=datetime.now())
        self.db.add(annotation)
        self.db.commit()
        content = { "transactionId": "1", "message": "Annotation created successfully", "data": {"id": annotation.id, "userName": user.name, "userId": annotation.user_id, "createdAt": annotation.created_at.isoformat()}}
        self.db.close()
        return JSONResponse(content=content, status_code=200)
    
    def get_annotations(self, model_id: str):
        records = (self.db.query(ProjectAnnotation).filter(ProjectAnnotation.model_id == model_id).order_by(ProjectAnnotation.created_at.desc()).all())
        annotations = []
        for annotation in records:   
            annotations.append({"id": annotation.id, "projectId": annotation.project_id, "modelId": annotation.model_id, "userId": annotation.user_id, "userName": annotation.user_ref.name if annotation.user_ref else None, "isResolved": annotation.is_resolved, "createdAt": annotation.created_at.isoformat() if annotation.created_at else None, "updatedAt": annotation.updated_at.isoformat() if annotation.updated_at else None})
        content = {"transactionId": "1", "message": "Annotations retrieved successfully", "data": annotations}
        self.db.close()
        return JSONResponse(content=content, status_code=200)
    
    def delete_annotation(self, annotation_id: str):
        annotation = (self.db.query(ProjectAnnotation).filter(ProjectAnnotation.id == annotation_id).first())
        if not annotation:
            self.db.close()
            raise HTTPException(status_code=404, detail="Annotation not found")
        self.db.delete(annotation)
        self.db.commit()
        content = {"transactionId": "1", "message": "Annotation deleted successfully", "data": {"id": annotation_id}}
        self.db.close()
        return JSONResponse(content=content, status_code=200)
    
    def resolve_annotation(self, annotation_id: str):
        annotation = (self.db.query(ProjectAnnotation).filter(ProjectAnnotation.id == annotation_id).first())
        if not annotation:
            self.db.close()
            raise HTTPException(status_code=404, detail="Annotation not found")
        annotation.is_resolved = True
        annotation.updated_at = datetime.now()
        self.db.commit()
        content = {"transactionId": "1","message": "Annotation resolved successfully"}
        self.db.close()
        return JSONResponse(content=content, status_code=200)
    
    def unresolve_annotation(self, annotation_id: str):
        annotation = (self.db.query(ProjectAnnotation).filter(ProjectAnnotation.id == annotation_id).first())
        if not annotation:
            self.db.close()
            raise HTTPException(status_code=404, detail="Annotation not found")
        annotation.is_resolved = False
        annotation.updated_at = datetime.now()
        self.db.commit()
        content = {"transactionId": "1", "message": "Annotation marked as active successfully", "data": { "id": annotation.id, "isResolved": annotation.is_resolved, "updatedAt": annotation.updated_at.isoformat()}}
        self.db.close()
        return JSONResponse(content=content, status_code=200)
    
    def update_annotation(self, annotation_id: str, annotation_data: dict):
        annotation_record = (self.db.query(ProjectAnnotation).filter(ProjectAnnotation.id == annotation_id).first())
        if not annotation_record:
            self.db.close()
            raise HTTPException(status_code=404, detail="Annotation not found")
        annotation_record.annotation = annotation_data.get("annotation")
        annotation_record.updated_at = datetime.now()
        flag_modified(annotation_record, "annotation")
        self.db.commit()
        content = {"transactionId": "1", "message": "Annotation updated successfully", "data": {"id": annotation_record.id, "annotation": annotation_record.annotation, "updatedAt": annotation_record.updated_at.isoformat() if annotation_record.updated_at else None}}
        self.db.close()
        return JSONResponse(content=content, status_code=200)

