from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import DateTime, select, and_, exists, cast, String
from sqlalchemy.sql import func
from datetime import datetime
from .modelDB import User, Project, user_project_association
from fastapi.responses import JSONResponse
from ..utils.configurationManager import manage_configurations
import json


class UserDao:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, user_id: str):
        user = self.db.query(User).filter(User.id == user_id).first()
        self.db.close()
        return user

    # def get_projectsOld(self, user_id: str):
    #     # Primero, busquemos todos los project_ids asociados al user_id
    #     stmt = select(user_project_association.c.project_id).where(user_project_association.c.user_id == user_id)
    #     result = self.db.execute(stmt).fetchall()
    #     project_ids = [row.project_id for row in result]
    #     projects = self.db.query(Project).filter(Project.id.in_(project_ids)).all()
    #     projects_list = [project.project for project in projects]
    #     self.db.close()
    #     return {"projects": projects_list}

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

    def get_specific_project(self, user_id: str, project_id: str):
        user = self.get_by_id(user_id)
        if user:
            for project in user.projects:
                if project.id == project_id:
                    return project
        return None

    def delete_project(self,user_id: str, project_id: str):
        user = self.get_by_id(user_id)
        if user:
            for project in user.projects:
                if project.id == project_id:
                    return project
        return None

class ProjectDao:
    def __init__(self, db: Session):
        self.db = db

    def check_project_exists_by_id(self, user_id: str, project_id: str) -> bool:
        project_exists = self.db.query(exists().where(
            Project.id == project_id
        )).scalar()
        return project_exists

    def check_project_exists(self, user_id: str, project_json: dict) -> bool:
        project_json_str = json.dumps(project_json, sort_keys=True)
        project_exists = self.db.query(exists().where(
            and_(
                func.cast(Project.project, String) == project_json_str,
                user_project_association.c.user_id == user_id,
                user_project_association.c.project_id == Project.id
            )
        )).scalar()
        return project_exists

    def get_by_id(self, user_id: str, project_id: str):
        project = self.db.query(Project).filter(Project.id == project_id).first()
        if not project:
            self.db.close()
            raise HTTPException(status_code=404, detail="Project not found")

        role = None
        collaborators = []

        if not project.template:
            try:
                role_data = self.get_user_role(project_id, user_id)
                role = role_data["data"]["role"]
            except HTTPException as e:
                if e.status_code == 404:
                    role = None 
                else:
                    raise e 

            # Intentar obtener los colaboradores
            try:
                collaborators_data = self.get_users(project_id, user_id)
                collaborators = collaborators_data["data"]["users"]
            except Exception as e:
                collaborators = []

        project_data = {
            "id": project.id,
            "name": project.name,
            "owner_id": project.owner_id,
            "project": project.project,
            "template": project.template,
            "description": project.description,
            "source": project.source,
            "author": project.author,
            "date": project.date,
            "configuration": project.configuration,
            "is_collaborative": project.is_collaborative,
            "role": role,
            "collaborators": collaborators
        }

        self.db.close()
        return {"transactionId": "1", "message": "Ok", "data": {"project": project_data}}

    """
    def get_all_configurations(self, project_id: str):
        project = self.db.query(Project).filter(Project.id == project_id).first()
        try:
            if not project:
                raise HTTPException(status_code=404, detail="Project not found")
            if not project.configuration or 'configurations' not in project.configuration:
                return {"transactionId": "1", "message": "No configurations available", "data": []}
            return {"transactionId": "1", "message": "Configurations retrieved successfully",
                    "data": project.configuration['configurations']}
        except HTTPException as e:
            raise e
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    def get_configuration(self, project_id: str, configuration_id: str):
        project = self.db.query(Project).filter(Project.id == project_id).first()
        try:
            if not project:
                raise HTTPException(status_code=404, detail="Project not found")
            for config in project.configuration['configurations']:
                if config['id'] == configuration_id:
                    return {"transactionId": "1", "message": "Configuration found", "data": config}

            raise HTTPException(status_code=404, detail="Configuration not found")
        except HTTPException as e:
            raise e
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    def apply_configuration(self, project_id, configuration_id: str):
        project = self.db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        project_data = project.project
        configurations = project.configuration.get("configurations", [])
        configuration = next((config for config in configurations if config["id"] == configuration_id), None)
        if not configuration:
            raise HTTPException(status_code=404, detail="Configuration not found")
        try:
            feature_values = {feature['id']: feature['properties'] for feature in configuration['features']}

            # Aplicar los valores de configuración a las propiedades del proyecto
            for product_line in project_data['productLines']:
                for model in product_line['domainEngineering']['models']:
                    for element in model['elements']:
                        if element['id'] in feature_values:
                            for prop in element['properties']:
                                    prop['properties'] = feature_values[element['id']]

            return {"transactionId": "1", "message": "Configuration applied successfully", "data": project_data}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    """

    def apply_configuration(self, project_id, model_id, configuration_id: str):
        # Recuperar el proyecto
        project = self.db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        # Verificar y extraer las configuraciones del modelo especificado
        model_configurations = project.configuration.get('modelConfigurations', {}).get(model_id, [])
        configuration = next((config for config in model_configurations if config['id'] == configuration_id), None)
        if not configuration:
            raise HTTPException(status_code=404, detail="Configuration not found")

        try:
            # Construir un diccionario de los valores de las características configuradas
            feature_values = {}
            for feature in configuration['features']:
                for prop in feature.get('properties', []):
                    if 'id' in prop and 'value' in prop:
                        feature_values[prop['id']] = prop['value']

            # Aplicar la configuración a las características del modelo especificado
            for product_line in project.project['productLines']:
                # Recorrer los modelos en domainEngineering
                if 'domainEngineering' in product_line:
                    for model in product_line['domainEngineering'].get('models', []):
                        if model['id'] == model_id:
                            for element in model['elements']:
                                for prop in element.get('properties', []):
                                    if prop['id'] in feature_values:
                                        prop['value'] = feature_values[prop['id']]

                # Recorrer los modelos en applicationEngineering
                if 'applicationEngineering' in product_line:
                    for model in product_line['applicationEngineering'].get('models', []):
                        if model['id'] == model_id:
                            for element in model['elements']:
                                for prop in element.get('properties', []):
                                    if prop['id'] in feature_values:
                                        prop['value'] = feature_values[prop['id']]

                # Recorrer los modelos en scope
                if 'scope' in product_line:
                    for model in product_line['scope'].get('models', []):
                        if model['id'] == model_id:
                            for element in model['elements']:
                                for prop in element.get('properties', []):
                                    if prop['id'] in feature_values:
                                        prop['value'] = feature_values[prop['id']]

            # Devolver el proyecto modificado como JSON sin modificar la base de datos
            print(project.project)
            return {"transactionId": "1", "message": "Configuration applied successfully", "data": project.project}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    def get_model_configurations(self, project_id: str, model_id: str):
        project = self.db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        if 'modelConfigurations' not in project.configuration:
            return {"transactionId": "1", "message": "No configurations available", "data": []}

        model_configs = project.configuration['modelConfigurations'].get(model_id, [])
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

    """
    def create_project(self, project_dict: dict, template : bool, user_id: str):
        print("creando proyecto...")
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
            self.db.close()
            raise Exception("El usuario no existe")
        # Configuración inicial vacía
        initial_configuration = {
            "idModel": str(uuid4()),
            "nameApplication": project_dict.get("name"),  # Tomando el nombre del proyecto como nombre de la aplicación
            "configurations": []
        }
        #id = str(uuid4())
        project = Project(id=str(uuid4()), name=project_dict.get("name"), project=project_dict,
                          template=template, configuration=initial_configuration)
        self.db.add(project)
        self.db.flush()  # Obtener el ID de proyecto recién creado antes de commitear
        # Asociar el proyecto con el usuario en la tabla de asociación
        assoc = user_project_association.insert().values(user_id=user_id, project_id=project.id)
        self.db.execute(assoc)
        self.db.commit()
        print("proyecto creado")
        content = {"transactionId": "1", "message": "Project created successfully", "data": {"id": project.id}}
        self.db.close()
        return JSONResponse(content=content, status_code=200)
    """


    def create_project(self, project_dict: dict, user_id: str):
        print("creando proyecto...")
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
            self.db.close()
            raise Exception("El usuario no existe")
        initial_configuration = {  # Lista de configuraciones ahora por modelID
        }
        project = Project(id=str(uuid4()), owner_id = user_id ,name=project_dict.get("name"), description=project_dict.get("description"), author=project_dict.get("author"), source=project_dict.get("source"), date= datetime.now(), project=project_dict.get("project"),
                          template=project_dict.get("template"), configuration=initial_configuration)
        self.db.add(project)
        self.db.flush()  # Obtener el ID de proyecto recién creado antes de commitear
        # Asociar el proyecto con el usuario en la tabla de asociación
        assoc = user_project_association.insert().values(user_id=user_id, project_id=project.id, role="owner")
        self.db.execute(assoc)
        self.db.commit()
        print("proyecto creado")
        content = {"transactionId": "1", "message": "Project created successfully", "data": {"id": project.id}}
        self.db.close()
        return JSONResponse(content=content, status_code=200)

    def update_project(self, project_dict: dict, user_id: str):
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
            self.db.close()
            raise Exception("El usuario no existe")

        id = project_dict.get("id")
        project = Project(id=id, name=project_dict.get("name"), description=project_dict.get("description"), author=project_dict.get("author"), source=project_dict.get("source"), date= datetime.now(), project=project_dict.get("project"),
                          template=project_dict.get("template"))
        self.db.query(Project).filter(Project.id == project.id).update(
            {"name": project.name, "project": project.project, "template": project.template, "description": project.description, "author":project.author, "source":project.source,"date":project.date})

        self.db.commit()
        content = {"transactionId": "1", "message": "Project updated successfully", "data": {"id": id}}
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

    def add_configuration(self, project_id: str, project_json : dict, id_feature_model, config_name : str):
        project = self.db.query(Project).filter(Project.id == project_id).first()
        if not project:
            self.db.close()
            raise HTTPException(status_code=404, detail="Project not found")
        if 'configuration' not in project.project or project.project['configuration'] is None:
            project.project['configuration'] = {}

        project.configuration= manage_configurations(project_json, id_feature_model, config_name, project.configuration)
        print("project configuration: ")
        print(project.configuration)
        flag_modified(project, "configuration")
        self.db.commit()
        content = {"transactionId": "1", "message": "Project name updated successfully"}
        self.db.close()
        return JSONResponse(content=content, status_code=200)

    """
    def delete_configuration_from_project(self, project_id: str, configuration_id: str):
        # Buscar el proyecto por ID
        project = self.db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        # Asegurarse de que el proyecto tiene configuraciones
        if not project.configuration or 'configurations' not in project.configuration:
            raise HTTPException(status_code=404, detail="No configurations found in project")

        # Filtrar la configuración que se desea eliminar
        original_count = len(project.configuration['configurations'])
        project.configuration['configurations'] = [
            config for config in project.configuration['configurations'] if config['id'] != configuration_id
        ]

        # Verificar si se eliminó alguna configuración
        if original_count == len(project.configuration['configurations']):
            raise HTTPException(status_code=404, detail="Configuration not found")

        # Guardar los cambios en la base de datos
        flag_modified(project, "configuration")
        self.db.commit()
        content = {"transactionId": "1", "message": "Project deleted successfully"}
        return JSONResponse(content=content, status_code=200)
        """

    def delete_configuration_from_project(self, project_id: str, model_id: str, configuration_id: str):
        project = self.db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        model_configurations = project.configuration.get('modelConfigurations', {}).get(model_id, [])
        original_count = len(model_configurations)
        updated_configurations = [config for config in model_configurations if config['id'] != configuration_id]

        if len(updated_configurations) == original_count:
            raise HTTPException(status_code=404, detail="Configuration not found")

        # Update the configurations for the specific model
        project.configuration['modelConfigurations'][model_id] = updated_configurations
        flag_modified(project, "configuration")  # Mark the 'configuration' attribute as modified
        self.db.commit()
        content = {"transactionId": "1", "message": "Configuration deleted successfully"}
        self.db.close()
        return JSONResponse(content=content, status_code=200)

    def delete_project(self, project_dict: dict):
        id=project_dict.get("id")
        project = self.db.query(Project).filter(Project.id == id).first()
        if not project:
            self.db.close()
            raise Exception("Project not found")
        # ELIMINAR ASOCIACIONES DE USUARIOS
        self.db.execute(user_project_association.delete().where(user_project_association.c.project_id == id))

        self.db.delete(project)
        self.db.commit()
        
        content = {"transactionId": "1", "message": "Project deleted successfully", "data": {"id": id}}
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

