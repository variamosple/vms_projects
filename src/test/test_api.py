import pytest
from fastapi.testclient import TestClient
from main_api import app
from ..utils.configurationManager import read_json_file

import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)

@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client

@pytest.fixture
def token(client):
    logger.info("realizando peticion de token")
    response = client.post("/token", json={"user_id": "1621858f-2dd9-4c83-b25a-ef4944144220"})
    response_data= response.json()  # Devuelve el JSON de la respuesta
    token = response_data['data']['access_token']
    logger.info("Token: %s", token)
    return token
@pytest.fixture
def project_data():
    # Aquí cargas o simulas los datos de un proyecto
    return {
        "id": "f81a7e46-f744-44b3-8be4-e0f1fe893be9",
        "name": "Product lines",
        "enable": True,
        "productLines": [
            {
                "id": "e95ada35-ac99-42b3-856c-42e0f42b0f84",
                "name": "My product line",
                "type": "System",
                "domain": "Retail",
                "domainEngineering": {
                    "models": [
                        {
                            "id": "a3d70a9f-05cc-4b72-a51c-c53abb7b467e",
                            "name": "Feature model without attributes",
                            "type": "Feature model without attributes",
                            "elements": [
                                {
                                    "id": "fd39ed72-87ba-4da5-8155-f2661f915760",
                                    "type": "RootFeature",
                                    "name": "GestorAireTemuco",
                                    "x": 450,
                                    "y": 30,
                                    "width": 100,
                                    "height": 33,
                                    "parentId": None,
                                    "properties": [
                                        {
                                            "id": "86bbf476-9f0e-496f-b360-e0424744246c",
                                            "name": "Selected",
                                            "value": "Undefined",
                                            "type": "String",
                                            "custom": False,
                                            "display": True,
                                            "comment": "Selected",
                                            "possibleValues": "Undefined,Selected,Unselected"
                                        }
                                    ]
                                },
                                {
                                    "id": "62215b44-1fe5-4837-9a54-69961e1d48e2",
                                    "type": "ConcreteFeature",
                                    "name": "VisualizadorCalidadAire",
                                    "x": 650,
                                    "y": 100,
                                    "width": 100,
                                    "height": 33,
                                    "parentId": None,
                                    "properties": [
                                        {
                                            "id": "718a9089-210a-48a0-8579-c26495b9694d",
                                            "name": "Selected",
                                            "value": "Undefined",
                                            "type": "String",
                                            "custom": False,
                                            "display": True,
                                            "comment": "Selected",
                                            "possibleValues": "Undefined,Selected,Unselected"
                                        }
                                    ]
                                },
                                {
                                    "id": "072f129d-126f-4783-bd0c-acdb079b1717",
                                    "type": "ConcreteFeature",
                                    "name": "VisualizadorRestriccionUsoLena",
                                    "x": 750,
                                    "y": 170,
                                    "width": 100,
                                    "height": 33,
                                    "parentId": None,
                                    "properties": [
                                        {
                                            "id": "c6bbc49e-6162-43a4-8e7a-ee181aceeb8f",
                                            "name": "Selected",
                                            "value": "Undefined",
                                            "type": "String",
                                            "custom": False,
                                            "display": True,
                                            "comment": "Selected",
                                            "possibleValues": "Undefined,Selected,Unselected"
                                        }
                                    ]
                                },
                                {
                                    "id": "6ed53de0-73cf-4a3a-8d86-cdac93c7858a",
                                    "type": "ConcreteFeature",
                                    "name": "Turismo",
                                    "x": 650,
                                    "y": 240,
                                    "width": 100,
                                    "height": 33,
                                    "parentId": None,
                                    "properties": [
                                        {
                                            "id": "f9bf94c6-357f-4f36-b928-a3c3086f044a",
                                            "name": "Selected",
                                            "value": "Undefined",
                                            "type": "String",
                                            "custom": False,
                                            "display": True,
                                            "comment": "Selected",
                                            "possibleValues": "Undefined,Selected,Unselected"
                                        }
                                    ]
                                },
                                {
                                    "id": "6453ca4f-8a21-44be-b8c0-ce36c4052e83",
                                    "type": "Bundle",
                                    "name": "Bundle 1",
                                    "x": 750,
                                    "y": 310,
                                    "width": 100,
                                    "height": 50,
                                    "parentId": None,
                                    "properties": [
                                        {
                                            "id": "4f6c1a00-314c-4255-ab87-ac0f25a1c8d3",
                                            "name": "Type",
                                            "value": "Xor",
                                            "type": "String",
                                            "custom": False,
                                            "display": True,
                                            "comment": "type options",
                                            "possibleValues": "And,Or,Xor,Range"
                                        }
                                    ]
                                },
                                {
                                    "id": "86ea484e-0601-4701-b56c-9454723182cb",
                                    "type": "ConcreteFeature",
                                    "name": "AmbienteCerrado",
                                    "x": 850,
                                    "y": 380,
                                    "width": 100,
                                    "height": 33,
                                    "parentId": None,
                                    "properties": [
                                        {
                                            "id": "1ac4ed5d-e231-4fc0-9911-833c27d6e498",
                                            "name": "Selected",
                                            "value": "Undefined",
                                            "type": "String",
                                            "custom": False,
                                            "display": True,
                                            "comment": "Selected",
                                            "possibleValues": "Undefined,Selected,Unselected"
                                        }
                                    ]
                                },
                                {
                                    "id": "7b4fed12-ad03-445f-bead-c51d77c04361",
                                    "type": "ConcreteFeature",
                                    "name": "AmbienteAbierto",
                                    "x": 850,
                                    "y": 450,
                                    "width": 100,
                                    "height": 33,
                                    "parentId": None,
                                    "properties": [
                                        {
                                            "id": "a3f1c5fa-d700-44ef-8bb0-bc0a7a819be6",
                                            "name": "Selected",
                                            "value": "Undefined",
                                            "type": "String",
                                            "custom": False,
                                            "display": True,
                                            "comment": "Selected",
                                            "possibleValues": "Undefined,Selected,Unselected"
                                        }
                                    ]
                                },
                                {
                                    "id": "668ce326-075e-4c60-bf64-a5ae003f97d5",
                                    "type": "ConcreteFeature",
                                    "name": "Deportes",
                                    "x": 650,
                                    "y": 520,
                                    "width": 100,
                                    "height": 33,
                                    "parentId": None,
                                    "properties": [
                                        {
                                            "id": "9b57b897-4459-44ed-acbc-85a7cc2507a4",
                                            "name": "Selected",
                                            "value": "Undefined",
                                            "type": "String",
                                            "custom": False,
                                            "display": True,
                                            "comment": "Selected",
                                            "possibleValues": "Undefined,Selected,Unselected"
                                        }
                                    ]
                                },
                                {
                                    "id": "9f494d3f-4706-4969-b2b4-c0d9684dee93",
                                    "type": "ConcreteFeature",
                                    "name": "Entretenimiento",
                                    "x": 650,
                                    "y": 590,
                                    "width": 100,
                                    "height": 33,
                                    "parentId": None,
                                    "properties": [
                                        {
                                            "id": "754f68cc-82dc-4361-a0ae-8430b40ed96b",
                                            "name": "Selected",
                                            "value": "Undefined",
                                            "type": "String",
                                            "custom": False,
                                            "display": True,
                                            "comment": "Selected",
                                            "possibleValues": "Undefined,Selected,Unselected"
                                        }
                                    ]
                                },
                                {
                                    "id": "28483b24-4ce8-403b-b1a5-ac0cc1603549",
                                    "type": "Bundle",
                                    "name": "Bundle 1",
                                    "x": 750,
                                    "y": 660,
                                    "width": 100,
                                    "height": 50,
                                    "parentId": None,
                                    "properties": [
                                        {
                                            "id": "88072132-e8c4-46ab-9fcd-8e3f67b08979",
                                            "name": "Type",
                                            "value": "Or",
                                            "type": "String",
                                            "custom": False,
                                            "display": True,
                                            "comment": "type options",
                                            "possibleValues": "And,Or,Xor,Range"
                                        }
                                    ]
                                },
                                {
                                    "id": "6533d678-2a19-47af-b78e-1a3083ed1e13",
                                    "type": "ConcreteFeature",
                                    "name": "EntrFamiliar",
                                    "x": 850,
                                    "y": 730,
                                    "width": 100,
                                    "height": 33,
                                    "parentId": None,
                                    "properties": [
                                        {
                                            "id": "0be68db7-5edc-4adb-b9ea-887e64a47397",
                                            "name": "Selected",
                                            "value": "Undefined",
                                            "type": "String",
                                            "custom": False,
                                            "display": True,
                                            "comment": "Selected",
                                            "possibleValues": "Undefined,Selected,Unselected"
                                        }
                                    ]
                                },
                                {
                                    "id": "d70d9207-587d-4fa9-b29a-a076e2e2609e",
                                    "type": "ConcreteFeature",
                                    "name": "EntrAdulto",
                                    "x": 850,
                                    "y": 800,
                                    "width": 100,
                                    "height": 33,
                                    "parentId": None,
                                    "properties": [
                                        {
                                            "id": "3d32ab3f-9054-4f91-86af-549c1b7ebf0b",
                                            "name": "Selected",
                                            "value": "Undefined",
                                            "type": "String",
                                            "custom": False,
                                            "display": True,
                                            "comment": "Selected",
                                            "possibleValues": "Undefined,Selected,Unselected"
                                        }
                                    ]
                                },
                                {
                                    "id": "c590cacf-98b7-4010-9dd7-e30a59c42793",
                                    "type": "ConcreteFeature",
                                    "name": "EntrTerceraEdad",
                                    "x": 850,
                                    "y": 870,
                                    "width": 100,
                                    "height": 33,
                                    "parentId": None,
                                    "properties": [
                                        {
                                            "id": "55fab3a4-b5f5-4d04-8673-7ef94c5ea0b4",
                                            "name": "Selected",
                                            "value": "Undefined",
                                            "type": "String",
                                            "custom": False,
                                            "display": True,
                                            "comment": "Selected",
                                            "possibleValues": "Undefined,Selected,Unselected"
                                        }
                                    ]
                                }
                            ],
                            "relationships": [
                                {
                                    "id": "caa663dc-19c4-4e0d-b683-5fb47722e869",
                                    "type": "RootFeature_Feature",
                                    "name": "VisualizadorCalidadAire",
                                    "sourceId": "fd39ed72-87ba-4da5-8155-f2661f915760",
                                    "targetId": "62215b44-1fe5-4837-9a54-69961e1d48e2",
                                    "points": [],
                                    "min": 0,
                                    "max": 9999999,
                                    "properties": [
                                        {
                                            "id": "eba2e741-73d5-4738-b4fd-428b35dc9d15",
                                            "name": "Type",
                                            "value": "Mandatory",
                                            "type": "String",
                                            "custom": False,
                                            "display": True,
                                            "possibleValues": "Mandatory,Optional,Includes,Excludes,IndividualCardinality"
                                        },
                                        {
                                            "id": "114e4c25-1ba3-4703-ac4f-76b87567bd3a",
                                            "name": "MinValue",
                                            "type": "String",
                                            "linked_property": "Type",
                                            "linked_value": "IndividualCardinality",
                                            "custom": False,
                                            "display": False
                                        },
                                        {
                                            "id": "457d274b-5e8a-4998-b71b-7f216ec8cd44",
                                            "name": "MaxValue",
                                            "type": "String",
                                            "linked_property": "Type",
                                            "linked_value": "IndividualCardinality",
                                            "custom": False,
                                            "display": False
                                        },
                                        {
                                            "id": "3e4dc074-758d-443e-a91e-b3d5e09bb7cf",
                                            "name": "Constraint",
                                            "type": "String",
                                            "custom": False,
                                            "display": True,
                                            "comment": "Constraint"
                                        }
                                    ]
                                },
                                {
                                    "id": "506e47fe-37c2-408d-88eb-1281c6fecba6",
                                    "type": "ConcreteFeature_Feature",
                                    "name": "VisualizadorRestriccionUsoLena",
                                    "sourceId": "62215b44-1fe5-4837-9a54-69961e1d48e2",
                                    "targetId": "072f129d-126f-4783-bd0c-acdb079b1717",
                                    "points": [],
                                    "min": 0,
                                    "max": 9999999,
                                    "properties": [
                                        {
                                            "id": "7446bf12-1ac4-482f-aa20-0fae888b9304",
                                            "name": "Type",
                                            "value": "Optional",
                                            "type": "String",
                                            "custom": False,
                                            "display": True,
                                            "possibleValues": "Mandatory,Optional,Includes,Excludes,IndividualCardinality"
                                        },
                                        {
                                            "id": "f9c7ea81-ed51-4279-aa19-41dd32b872cd",
                                            "name": "MinValue",
                                            "type": "String",
                                            "linked_property": "Type",
                                            "linked_value": "IndividualCardinality",
                                            "custom": False,
                                            "display": False
                                        },
                                        {
                                            "id": "c4baf536-a812-481f-b595-f6d9aff38e94",
                                            "name": "MaxValue",
                                            "type": "String",
                                            "linked_property": "Type",
                                            "linked_value": "IndividualCardinality",
                                            "custom": False,
                                            "display": False
                                        },
                                        {
                                            "id": "94253cb1-d8d6-4e44-8d46-40f192449eed",
                                            "name": "Constraint",
                                            "type": "String",
                                            "custom": False,
                                            "display": True,
                                            "comment": "Constraint"
                                        }
                                    ]
                                },
                                {
                                    "id": "1bbfe0dd-0a84-442c-92fe-255515d96aa5",
                                    "type": "RootFeature_Feature",
                                    "name": "Turismo",
                                    "sourceId": "fd39ed72-87ba-4da5-8155-f2661f915760",
                                    "targetId": "6ed53de0-73cf-4a3a-8d86-cdac93c7858a",
                                    "points": [],
                                    "min": 0,
                                    "max": 9999999,
                                    "properties": [
                                        {
                                            "id": "8c340181-1f4e-46e6-b98c-e13ac30d4f6d",
                                            "name": "Type",
                                            "value": "Mandatory",
                                            "type": "String",
                                            "custom": False,
                                            "display": True,
                                            "possibleValues": "Mandatory,Optional,Includes,Excludes,IndividualCardinality"
                                        },
                                        {
                                            "id": "067bc663-3f4f-4ba3-9eb4-4096c297fbfe",
                                            "name": "MinValue",
                                            "type": "String",
                                            "linked_property": "Type",
                                            "linked_value": "IndividualCardinality",
                                            "custom": False,
                                            "display": False
                                        },
                                        {
                                            "id": "2f8431ba-faba-4c43-ad71-ed0893763144",
                                            "name": "MaxValue",
                                            "type": "String",
                                            "linked_property": "Type",
                                            "linked_value": "IndividualCardinality",
                                            "custom": False,
                                            "display": False
                                        },
                                        {
                                            "id": "d7b01320-157d-493d-93c5-258a45a728ec",
                                            "name": "Constraint",
                                            "type": "String",
                                            "custom": False,
                                            "display": True,
                                            "comment": "Constraint"
                                        }
                                    ]
                                },
                                {
                                    "id": "878a9a67-4343-43fc-86ab-f2486f591a1f",
                                    "type": "Bundle_Feature",
                                    "name": "Xor",
                                    "sourceId": "6ed53de0-73cf-4a3a-8d86-cdac93c7858a",
                                    "targetId": "6453ca4f-8a21-44be-b8c0-ce36c4052e83",
                                    "points": [],
                                    "min": 0,
                                    "max": 9999999,
                                    "properties": []
                                },
                                {
                                    "id": "248b1796-072e-4f5a-b9f3-685b090b18bc",
                                    "type": "ConcreteFeature_Feature",
                                    "name": "AmbienteCerrado",
                                    "sourceId": "6453ca4f-8a21-44be-b8c0-ce36c4052e83",
                                    "targetId": "86ea484e-0601-4701-b56c-9454723182cb",
                                    "points": [],
                                    "min": 0,
                                    "max": 9999999,
                                    "properties": []
                                },
                                {
                                    "id": "31cd9ef6-dbd2-4b8c-871a-c0f784e1122b",
                                    "type": "ConcreteFeature_Feature",
                                    "name": "AmbienteAbierto",
                                    "sourceId": "6453ca4f-8a21-44be-b8c0-ce36c4052e83",
                                    "targetId": "7b4fed12-ad03-445f-bead-c51d77c04361",
                                    "points": [],
                                    "min": 0,
                                    "max": 9999999,
                                    "properties": []
                                },
                                {
                                    "id": "aa51bcd7-73c1-4967-9f32-d0f4529bb3c3",
                                    "type": "RootFeature_Feature",
                                    "name": "Deportes",
                                    "sourceId": "fd39ed72-87ba-4da5-8155-f2661f915760",
                                    "targetId": "668ce326-075e-4c60-bf64-a5ae003f97d5",
                                    "points": [],
                                    "min": 0,
                                    "max": 9999999,
                                    "properties": [
                                        {
                                            "id": "f89c1b38-0073-4a9c-851a-25934ff80df9",
                                            "name": "Type",
                                            "value": "Optional",
                                            "type": "String",
                                            "custom": False,
                                            "display": True,
                                            "possibleValues": "Mandatory,Optional,Includes,Excludes,IndividualCardinality"
                                        },
                                        {
                                            "id": "0a5ee57e-65b6-4ad2-8beb-0c6b5b8435ee",
                                            "name": "MinValue",
                                            "type": "String",
                                            "linked_property": "Type",
                                            "linked_value": "IndividualCardinality",
                                            "custom": False,
                                            "display": False
                                        },
                                        {
                                            "id": "4690d9d2-051f-4848-92a3-efd2f245e300",
                                            "name": "MaxValue",
                                            "type": "String",
                                            "linked_property": "Type",
                                            "linked_value": "IndividualCardinality",
                                            "custom": False,
                                            "display": False
                                        },
                                        {
                                            "id": "49fa261f-1401-48da-9f13-4b577d4f0e82",
                                            "name": "Constraint",
                                            "type": "String",
                                            "custom": False,
                                            "display": True,
                                            "comment": "Constraint"
                                        }
                                    ]
                                },
                                {
                                    "id": "06818af0-911d-4665-9b3b-733604931672",
                                    "type": "RootFeature_Feature",
                                    "name": "Entretenimiento",
                                    "sourceId": "fd39ed72-87ba-4da5-8155-f2661f915760",
                                    "targetId": "9f494d3f-4706-4969-b2b4-c0d9684dee93",
                                    "points": [],
                                    "min": 0,
                                    "max": 9999999,
                                    "properties": [
                                        {
                                            "id": "a6019b9a-7726-4c08-9da9-17a27907bd65",
                                            "name": "Type",
                                            "value": "Optional",
                                            "type": "String",
                                            "custom": False,
                                            "display": True,
                                            "possibleValues": "Mandatory,Optional,Includes,Excludes,IndividualCardinality"
                                        },
                                        {
                                            "id": "de553dcf-d1b4-46ed-8cfa-07a93f85aae7",
                                            "name": "MinValue",
                                            "type": "String",
                                            "linked_property": "Type",
                                            "linked_value": "IndividualCardinality",
                                            "custom": False,
                                            "display": False
                                        },
                                        {
                                            "id": "a85758d9-b5f7-4d6d-9bac-c45dea3118ca",
                                            "name": "MaxValue",
                                            "type": "String",
                                            "linked_property": "Type",
                                            "linked_value": "IndividualCardinality",
                                            "custom": False,
                                            "display": False
                                        },
                                        {
                                            "id": "2975444e-8bd4-44e1-8578-4e41ceb80bb0",
                                            "name": "Constraint",
                                            "type": "String",
                                            "custom": False,
                                            "display": True,
                                            "comment": "Constraint"
                                        }
                                    ]
                                },
                                {
                                    "id": "a84c7897-2afa-45b8-9c5f-be93ffc358d4",
                                    "type": "Bundle_Feature",
                                    "name": "Or",
                                    "sourceId": "9f494d3f-4706-4969-b2b4-c0d9684dee93",
                                    "targetId": "28483b24-4ce8-403b-b1a5-ac0cc1603549",
                                    "points": [],
                                    "min": 0,
                                    "max": 9999999,
                                    "properties": []
                                },
                                {
                                    "id": "6e7ee28c-afc7-41c0-bbf4-f0613709975c",
                                    "type": "ConcreteFeature_Feature",
                                    "name": "EntrFamiliar",
                                    "sourceId": "28483b24-4ce8-403b-b1a5-ac0cc1603549",
                                    "targetId": "6533d678-2a19-47af-b78e-1a3083ed1e13",
                                    "points": [],
                                    "min": 0,
                                    "max": 9999999,
                                    "properties": []
                                },
                                {
                                    "id": "877dd991-18e1-473a-a516-6e9565b9f75c",
                                    "type": "ConcreteFeature_Feature",
                                    "name": "EntrAdulto",
                                    "sourceId": "28483b24-4ce8-403b-b1a5-ac0cc1603549",
                                    "targetId": "d70d9207-587d-4fa9-b29a-a076e2e2609e",
                                    "points": [],
                                    "min": 0,
                                    "max": 9999999,
                                    "properties": []
                                },
                                {
                                    "id": "f836e2b4-8411-4621-a1cd-439b582b1427",
                                    "type": "ConcreteFeature_Feature",
                                    "name": "EntrTerceraEdad",
                                    "sourceId": "28483b24-4ce8-403b-b1a5-ac0cc1603549",
                                    "targetId": "c590cacf-98b7-4010-9dd7-e30a59c42793",
                                    "points": [],
                                    "min": 0,
                                    "max": 9999999,
                                    "properties": []
                                },
                                {
                                    "id": "7f2aada0-38d0-41cd-b9cd-13aeed4a8cec",
                                    "type": "ConcreteFeature_Feature",
                                    "name": "AmbienteAbierto_to_Deportes",
                                    "sourceId": "7b4fed12-ad03-445f-bead-c51d77c04361",
                                    "targetId": "668ce326-075e-4c60-bf64-a5ae003f97d5",
                                    "points": [],
                                    "min": 0,
                                    "max": 9999999,
                                    "properties": [
                                        {
                                            "id": "546f7fcf-7a88-416d-ad73-e4afd220f720",
                                            "name": "Type",
                                            "value": "Includes",
                                            "type": "String",
                                            "custom": False,
                                            "display": True,
                                            "possibleValues": "Mandatory,Optional,Includes,Excludes,IndividualCardinality"
                                        },
                                        {
                                            "id": "6f9abbdb-e22a-4c6d-807e-f020d7c09674",
                                            "name": "MinValue",
                                            "type": "String",
                                            "linked_property": "Type",
                                            "linked_value": "IndividualCardinality",
                                            "custom": False,
                                            "display": False
                                        },
                                        {
                                            "id": "6e1fe3ca-66db-481b-bce7-97f4c15f0bef",
                                            "name": "MaxValue",
                                            "type": "String",
                                            "linked_property": "Type",
                                            "linked_value": "IndividualCardinality",
                                            "custom": False,
                                            "display": False
                                        },
                                        {
                                            "id": "e302644c-567f-45a1-ac14-05d0763ac58a",
                                            "name": "Constraint",
                                            "type": "String",
                                            "custom": False,
                                            "display": True,
                                            "comment": "Constraint"
                                        }
                                    ]
                                },
                                {
                                    "id": "d5cdad1f-4d4e-4855-9c32-24029c8a227f",
                                    "type": "ConcreteFeature_Feature",
                                    "name": "AmbienteCerrado_to_VisualizadorRestriccionUsoLena",
                                    "sourceId": "86ea484e-0601-4701-b56c-9454723182cb",
                                    "targetId": "072f129d-126f-4783-bd0c-acdb079b1717",
                                    "points": [],
                                    "min": 0,
                                    "max": 9999999,
                                    "properties": [
                                        {
                                            "id": "7e1d6071-526e-4859-9c3a-594dec2cd4c8",
                                            "name": "Type",
                                            "value": "Includes",
                                            "type": "String",
                                            "custom": False,
                                            "display": True,
                                            "possibleValues": "Mandatory,Optional,Includes,Excludes,IndividualCardinality"
                                        },
                                        {
                                            "id": "a94e754a-84a9-44cd-b1b7-1c8604a5d6f5",
                                            "name": "MinValue",
                                            "type": "String",
                                            "linked_property": "Type",
                                            "linked_value": "IndividualCardinality",
                                            "custom": False,
                                            "display": False
                                        },
                                        {
                                            "id": "b6422ccc-1a41-435c-81c9-2722a661ce83",
                                            "name": "MaxValue",
                                            "type": "String",
                                            "linked_property": "Type",
                                            "linked_value": "IndividualCardinality",
                                            "custom": False,
                                            "display": False
                                        },
                                        {
                                            "id": "5333ee40-037e-4de3-9b18-fdf9f983bc31",
                                            "name": "Constraint",
                                            "type": "String",
                                            "custom": False,
                                            "display": True,
                                            "comment": "Constraint"
                                        }
                                    ]
                                },
                                {
                                    "id": "03e393c3-400a-4bd7-9d30-28fc2b21def8",
                                    "type": "ConcreteFeature_Feature",
                                    "name": "AmbienteCerrado_to_EntrTerceraEdad",
                                    "sourceId": "86ea484e-0601-4701-b56c-9454723182cb",
                                    "targetId": "c590cacf-98b7-4010-9dd7-e30a59c42793",
                                    "points": [],
                                    "min": 0,
                                    "max": 9999999,
                                    "properties": [
                                        {
                                            "id": "c00cc93b-4ed0-4a88-b0ae-41f3f990bd1b",
                                            "name": "Type",
                                            "value": "Excludes",
                                            "type": "String",
                                            "custom": False,
                                            "display": True,
                                            "possibleValues": "Mandatory,Optional,Includes,Excludes,IndividualCardinality"
                                        },
                                        {
                                            "id": "e07be875-bb4b-4d46-a824-7c252e4d4d74",
                                            "name": "MinValue",
                                            "type": "String",
                                            "linked_property": "Type",
                                            "linked_value": "IndividualCardinality",
                                            "custom": False,
                                            "display": False
                                        },
                                        {
                                            "id": "3c5ffe8c-8128-4d98-a686-039c11bee0af",
                                            "name": "MaxValue",
                                            "type": "String",
                                            "linked_property": "Type",
                                            "linked_value": "IndividualCardinality",
                                            "custom": False,
                                            "display": False
                                        },
                                        {
                                            "id": "87674528-ac08-4cc4-a8fd-f8411194ff46",
                                            "name": "Constraint",
                                            "type": "String",
                                            "custom": False,
                                            "display": True,
                                            "comment": "Constraint"
                                        }
                                    ]
                                }
                            ],
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
            }
        ]
    }

"""
@pytest.fixture
def user_id():
    # Aquí cargas o simulas los datos de un proyecto
    return "1621858f-2dd9-4c83-b25a-ef4944144220"


@pytest.fixture
def config_id():
    # Aquí cargas o simulas los datos de un proyecto
    return ""


@pytest.fixture
def configuration():
    # Aquí cargas o simulas los datos de un proyecto
    return ""
"""

def test_save_project(client, project_data, token):
    print(token)
    headers = {"Authorization": f"Bearer {token}"}
    response = client.post("/saveProject", json=project_data, headers=headers)
    logger.info("ejecutando test_save_project")
    print(response.json())
    assert response.status_code == 200


def test_add_configuration(client,project_id,project_data):
    response = client.post("/addConfiguration", json={
        "project_id": project_id,
        "config_input": project_data
    })
    assert response.status_code == 200
    print(response.json())

def test_delete_configuration(client,project_id,config_id):
    response = client.delete("/deleteConfiguration", params={"project_id": project_id, "configuration_id": config_id})
    assert response.status_code == 200
    print(response.json())

def test_get_configuration(client,project_id,config_id):
    response = client.get("/getConfiguration", params={"project_id": project_id, "configuration_id": config_id})
    assert response.status_code == 200
    print(response.json())

def test_get_all_configurations(client,project_id):
    response = client.get("/getAllConfigurations", params={"project_id": project_id})
    assert response.status_code == 200
    print(response.json())

def test_apply_configuration(client, project_data, configuration):
    response = client.post("/applyConfiguration", json={"model_json": project_data, "configuration": configuration})
    assert response.status_code == 200
    print(response.json())

def test_create_project_history(client, token):
    headers = {"Authorization": f"Bearer {token}"}
    history_data = {
        "projectId": "94268b16-be83-4380-ba36-7514fed4875c",
        "modelId": "ce19115c-4406-4160-a017-519eb4db8d16",
        "actionType": "ITEM_UPDATED",
        "entityType": "ELEMENT",
        "entityId": "1d18eeb9-c066-474e-8c7b-0b12c649d139",
        "entityName": "Gato",
        "oldValue": {
            "name": "Gato"
        },
        "newValue": {
            "name": "Perro"
        },
        "description": "Updated element \"Gato\": name"
    }
    response = client.post("/projectHistory", json=history_data, headers=headers)
    print(response.json())
    assert response.status_code == 200

def test_get_project_history(client, token):
    headers = {"Authorization": f"Bearer {token}"}
    project_id = "94268b16-be83-4380-ba36-7514fed4875c"
    response = client.get("/projectHistory", params={"project_id": project_id}, headers=headers)
    print(response.json())
    assert response.status_code == 200
