import json
import uuid

def read_uvl_file(file_path):
    with open(file_path, 'r', encoding='utf-8') as file:
        return file.read()

def parse_feature_line(line):
    # Identificar si la línea es un constraint o define una característica/relación
    if "=>" in line:
        parts = line.split("=>")
        source = parts[0].strip()
        target = parts[1].strip().replace("!", "").strip()  # Eliminar "!" para "excludes"
        relation_type = "Excludes" if "!" in parts[1] else "Includes"
        return relation_type, source, target, True  # True indica que es un constraint
    else:
        parts = line.split(maxsplit=1)
        if len(parts) == 2:
            name, abstract = parts[0].replace('"', '').strip(), parts[1].replace('{', '').replace('}', '').strip()
            return None, name, abstract, False
        else:
            name = parts[0].replace('"', '').strip()  # Solo nombre de característica
            return None, name, None, False  # False indica que no es un constraint


def generate_feature(id, name, type, x, y):
    return {
        "id": id,
        "type": type,
        "name": name,
        "x": x,
        "y": y,
        "width": 100,
        "height": 33,
        "parentId": None,
        "properties": [{
            "id": str(uuid.uuid4()),
            "name": "Selected",
            "value": "Undefined",
            "type": "String",
            "custom": False,
            "display": True,
            "comment": "Selected",
            "possibleValues": "Undefined,Selected,Unselected"
        }]
    }

def generate_relationship(source_id, target_id, relation_type, name, source_feature_type):
    if source_feature_type == "RootFeature":
        relationship_type = "RootFeature_Feature"
    elif source_feature_type == "ConcreteFeature":
        relationship_type = "ConcreteFeature_Feature"
    elif source_feature_type == "AbstractFeature":
        relationship_type = "AbstractFeature_Feature"
    else:
        relationship_type = "ConcreteFeature_Feature"
    #print(source_feature_type)
    #print(relationship_type)
    properties = []
    if relation_type not in ["Xor", "Or"]:  # No añadir propiedades si la relación es desde un bundle XOR u OR
        properties = [{
            "id": str(uuid.uuid4()),
            "name": "Type",
            "value": relation_type,
            "type": "String",
            "custom": False,
            "display": True,
            "possibleValues": "Mandatory,Optional,Includes,Excludes,IndividualCardinality"
        },{
			"id":  str(uuid.uuid4()),
            "name": "MinValue",
            "type": "String",
            "linked_property": "Type",
            "linked_value": "IndividualCardinality",
            "custom": False,
            "display": False
            },
            {
            "id":  str(uuid.uuid4()),
            "name": "MaxValue",
            "type": "String",
            "linked_property": "Type",
            "linked_value": "IndividualCardinality",
            "custom": False,
            "display": False
            },
            {
            "id":  str(uuid.uuid4()),
            "name": "Constraint",
            "type": "String",
            "custom": False,
            "display": True,
            "comment": "Constraint"
            }]
    return {
        "id": str(uuid.uuid4()),
        "type":  relationship_type,
        "name": name,
        "sourceId": source_id,
        "targetId": target_id,
        "points": [],
        "min": 0,
        "max": 9999999,
        "properties": properties
    }
"""
def parse_uvl_content(uvl_content):
        lines = uvl_content.strip().split("\n")
        elements = []
        relationships = []
        feature_stack = [(None, 0, 'Optional')]  # Inicialización ajustada para incluir tipo de relación
        name_to_id = {}
        x_base, y_base, y_increment = 350, 30, 70
        current_relation_type = 'Optional'  # Controla el tipo de relación actual
        processing_constraints = False

        for index, line in enumerate(lines):
            if not line.strip() or line.strip() == "" or index == 0:
                continue

            if 'features' in line.lower():
                current_relation_type = 'Optional'  # Restablece al default al empezar nuevas features
                continue
            elif 'constraints' in line.lower():
                processing_constraints = True
                continue

            indent_level = line.count("\t")
            parsed_line = parse_feature_line(line.strip().replace('"', ''))

            if processing_constraints:
                if parsed_line[3]:  # Si es un constraint (includes o excludes)
                    source_id = name_to_id.get(parsed_line[1])
                    target_id = name_to_id.get(parsed_line[2])
                    if source_id and target_id:
                        relationships.append(generate_relationship(source_id, target_id, parsed_line[0], f"{parsed_line[1]}_to_{parsed_line[2]}","ConcreteFeature"))
                continue

            relation_type, name, abstract, is_constraint = parsed_line

            if name.lower() in ["mandatory", "optional"]:
                current_relation_type = name.capitalize()
                continue  # No crea características para 'mandatory' o 'optional'

            while feature_stack and indent_level <= feature_stack[-1][1]:
                feature_stack.pop()

            if name.lower() in ["alternative", "or"]:
                bundle_type = "Xor" if name.lower() == "alternative" else "Or"
                bundle_id, bundle = create_bundle(x_base + indent_level * 100, y_base + len(elements) * y_increment, bundle_type)
                elements.append(bundle)
                name_to_id[bundle_type + str(indent_level)] = bundle_id  # Clave única para bundle en este nivel
                if feature_stack:
                    parent_id = feature_stack[-1][0]
                    # Crear relación desde el padre al bundle
                    relationships.append(create_bundle_feature_relation(parent_id, bundle_id, bundle_type))
                # Configurar este bundle como contexto actual para las próximas características
                feature_stack.append((bundle_id, indent_level, bundle_type, bundle_id))
                continue

            if not is_constraint and name.lower() not in ["mandatory", "optional", "alternative", "or"]:
                feature_id = str(uuid.uuid4())
                #print(f"indent level {indent_level}, name {name}")
                feature_type = "RootFeature" if indent_level == 1 else "ConcreteFeature"
                if(abstract != None):
                    feature_type= "AbstractFeature"
                x = x_base + indent_level * 100
                y = y_base + len(elements) * y_increment
                feature = generate_feature(feature_id, name, feature_type, x, y)
                elements.append(feature)
                name_to_id[name] = feature_id
                if feature_stack:
                    parent_id = feature_stack[-1][0]
                    if feature_stack[-1][2] in ["Xor", "Or"]:  # Si el padre es un bundle
                        relation_type = "Xor" if feature_stack[-1][2] == "Xor" else "Or"
                    else:
                        relation_type = current_relation_type
                    relationships.append(generate_relationship(parent_id, feature_id, relation_type, name,feature_type))

            feature_stack.append((feature_id, indent_level, current_relation_type))

        return elements, relationships
"""

def parse_uvl_content(uvl_content):
    lines = uvl_content.strip().split("\n")
    elements = []
    relationships = []
    feature_stack = []  # Stack now starts empty
    name_to_id = {}
    x_base, y_base, y_increment = 350, 30, 70
    current_relation_type = 'Optional'  # Default relationship type
    processing_constraints = False
    first_feature = True

    for index, line in enumerate(lines):
        if not line.strip() or line.strip() == "" or index == 0:
            continue

        if 'features' in line.lower():
            current_relation_type = 'Optional'  # Reset to default at the start of new features section
            continue
        elif 'constraints' in line.lower():
            processing_constraints = True
            continue

        indent_level = line.count("\t")
        parsed_line = parse_feature_line(line.strip().replace('"', ''))

        if processing_constraints:
            if parsed_line[3]:  # Constraint processing
                source_id = name_to_id.get(parsed_line[1])
                target_id = name_to_id.get(parsed_line[2])
                if source_id and target_id:
                    source_type = feature_stack[-1][3] if feature_stack else 'ConcreteFeature'
                    relationships.append(generate_relationship(source_id, target_id, parsed_line[0], f"{parsed_line[1]}_to_{parsed_line[2]}",source_type))
            continue

        relation_type, name, abstract, is_constraint = parsed_line

        if name.lower() in ["mandatory", "optional"]:
            current_relation_type = name.capitalize()
            continue  # Skip creating features for 'mandatory' or 'optional'

        # Pop the stack until the current level is deeper than the last feature's level
        while feature_stack and indent_level <= feature_stack[-1][1]:
            feature_stack.pop()

        if name.lower() in ["alternative", "or"]:
            bundle_type = "Xor" if name.lower() == "alternative" else "Or"
            bundle_id, bundle = create_bundle(x_base + indent_level * 100, y_base + len(elements) * y_increment,bundle_type)
            elements.append(bundle)
            name_to_id[bundle_type + str(indent_level)] = bundle_id  # Clave única para bundle en este nivel
            if feature_stack:
                parent_id = feature_stack[-1][0]
                # Crear relación desde el padre al bundle
                relationships.append(create_bundle_feature_relation(parent_id, bundle_id, bundle_type))
            # Configurar este bundle como contexto actual para las próximas características
            feature_stack.append((bundle_id, indent_level, bundle_type, bundle_id))
            continue

        feature_id = str(uuid.uuid4())
        feature_type = "RootFeature" if first_feature or indent_level == 0 else "ConcreteFeature"
        if abstract is not None:
            feature_type = "AbstractFeature"
        x = x_base + indent_level * 100
        y = y_base + len(elements) * y_increment
        feature = generate_feature(feature_id, name, feature_type, x, y)
        elements.append(feature)
        name_to_id[name] = feature_id
        first_feature = False

        # Only create relationships if there is a parent in the stack
        if feature_stack:
            parent_id, parent_indent, parent_relation_type, parent_feature_type = feature_stack[-1]
            if feature_stack[-1][2] in ["Xor", "Or"]:  # Si el padre es un bundle
                relation_type = "Xor" if feature_stack[-1][2] == "Xor" else "Or"
            else:
                relation_type = current_relation_type
            relationships.append(
                generate_relationship(parent_id, feature_id, relation_type, name, parent_feature_type))

        # Always add the current feature to the stack
        feature_stack.append((feature_id, indent_level, current_relation_type, feature_type))

    return elements, relationships



def generate_json(elements, relationships):
    model = {
        "id": str(uuid.uuid4()),
        "name": "Product lines",
        "enable": True,
        "productLines": [{
            "id": str(uuid.uuid4()),
            "name": "My product line",
            "type": "System",
            "domain": "Retail",
            "domainEngineering": {
                "models": [{
                    "id": str(uuid.uuid4()),
                    "name": "Feature model without attributes",
                    "type": "Feature model without attributes",
                    "elements": elements,
                    "relationships": relationships,
                    "constraints": ""
                }
                ],
                "languagesAllowed": []
            },
            "applicationEngineering": {
                "models": [],
                "languagesAllowed": [],
                "applications": []
            }
        }]
    }
    return json.dumps(model, indent=4)

def write_json_to_file(json_data, output_file):
    with open(output_file, 'w', encoding='utf-8') as file:
        file.write(json_data)

def uvl_to_json(input_file, output_file):
    uvl_content = read_uvl_file(input_file)
    elements, relationships = parse_uvl_content(uvl_content)
    json_data = generate_json(elements, relationships)
    write_json_to_file(json_data, output_file)

def create_bundle(x, y, relation_type):
    bundle_id = str(uuid.uuid4())
    bundle = {
        "id": bundle_id,
        "type": "Bundle",
        "name": "Bundle 1",
        "x": x,
        "y": y,
        "width": 100,
        "height": 50,
        "parentId": None,
        "properties": [{
            "id": str(uuid.uuid4()),
            "name": "Type",
            "value": relation_type,
            "type": "String",
            "custom": False,
            "display": True,
            "comment": "type options",
            "possibleValues": "And,Or,Xor,Range"
        }]
    }
    return bundle_id, bundle


def create_bundle_feature_relation(bundle_id, feature_id, name):
    return {
        "id": str(uuid.uuid4()),
        "type": "Bundle_Feature",
        "name": name,
        "sourceId": bundle_id,
        "targetId": feature_id,
        "points": [],
        "min": 0,
        "max": 9999999,
        "properties": []
    }
"""
# Adjust 'test.uvl' and 'output.json' with your actual input and output file paths as needed.
uvl_to_json('linux.uvl', 'linux.json')
uvl_to_json('test.uvl', 'test.json')
uvl_to_json('model1.uvl', 'model1.json')
uvl_to_json('model2.uvl', 'model2.json')
uvl_to_json('model3.uvl', 'model3.json')
uvl_to_json('model4.uvl', 'model4.json')
uvl_to_json('model5.uvl', 'model5.json')
uvl_to_json('smart_home.uvl', 'smart_home.json')
"""
#uvl_to_json('test.uvl', 'test.json')