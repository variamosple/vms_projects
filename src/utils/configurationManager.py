import uuid
import json
import os
from pathlib import Path

def read_json_file(filename):
    current_dir = Path(__file__).parent.parent.parent
    file_path = current_dir / filename
    with open(file_path, 'r', encoding='utf-8') as file:
        data = json.load(file)
    return data

def write_json_to_file(data, filename):
    with open(filename, 'w', encoding='utf-8') as file:
        json.dump(data, file, ensure_ascii=False, indent=4)


def extract_model_info(data, model_id, config_name):
    for product_line in data['productLines']:
        # Define the model categories to search
        model_categories = ['scope', 'domainEngineering', 'applicationEngineering']

        for category in model_categories:
            models = product_line.get(category, {}).get('models', [])

            for model in models:
                if model['id'] == model_id:
                    configurations = []
                    config_id = str(uuid.uuid4())
                    features = []
                    relationships = []

                    # Extract features
                    for element in model.get('elements', []):
                        feature = {
                            "id": element['id'],
                            "type": element['type'],
                            "name": element['name'],
                            "properties": element.get('properties', [])
                        }
                        features.append(feature)

                    # Extract relationships
                    for relationship in model.get('relationships', []):
                        relation = {
                            "id": relationship['id'],
                            "type": relationship['type'],
                            "properties": relationship.get('properties', [])
                        }
                        relationships.append(relation)

                    configuration = {
                        "id": config_id,
                        "name": config_name,
                        "features": features,
                        "relationships": relationships
                    }
                    configurations.append(configuration)

                    return {
                        "idModel": model_id,
                        "nameApplication": config_name,
                        "configurations": configurations
                    }

def manage_configurations(data, id, config_name, project_configuration):
    new_config = extract_model_info(data, id, config_name)
    if 'modelConfigurations' not in project_configuration:
        project_configuration['modelConfigurations'] = {}
    if id not in project_configuration['modelConfigurations']:
        project_configuration['modelConfigurations'][id] = []
    project_configuration['modelConfigurations'][id].append(new_config['configurations'][0])
    return project_configuration

