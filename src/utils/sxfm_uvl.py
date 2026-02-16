import re

from src.utils.arbol import read_uvl_file, write_json_to_file



def sxfm_to_uvl(sxfm_content):
    feature_tree_content = extract_section(sxfm_content, 'feature_tree')
    constraints_content = extract_section(sxfm_content, 'constraints')

    id_to_name, uvl_features = convert_feature_tree_to_uvl(feature_tree_content)
    uvl_constraints = convert_constraints_to_uvl(constraints_content, id_to_name)

    uvl_content = uvl_features + "\n" + uvl_constraints
    return uvl_content


def extract_section(content, section_name):
    pattern = f"<{section_name}>.*?</{section_name}>"
    match = re.search(pattern, content, re.DOTALL)
    if match:
        return match.group(0)
    return ""


def convert_feature_tree_to_uvl(feature_tree_content):
    # Limpiar IDs y ajustar para tabulación y estructura UVL
    lines = feature_tree_content.split("\n")
    uvl_lines = ["features"]
    id_to_name = {}
    indent_level = 1  # Comienza después de 'features'

    for line in lines:
        if line.strip():
            line_content = line.strip()
            indent_count = line.count("\t")  # Contar tabulaciones en el SXFM original
            new_indent = "\t" * (indent_count + 1)  # Ajustar indentación para UVL
            feature_match = re.match(r":([romg]*)\s+(\S+)\s*\((\S+)\)", line_content)
            if feature_match:
                type_prefix, feature_name, feature_id = feature_match.groups()
                feature_name = feature_name.replace("_", " ")
                print(f"caracteristica es {feature_name} y id es {feature_id}")
                id_to_name[feature_id] = feature_name
            if ":r" in line_content:
                #feature_name = line_content.split(" ")[1]
                uvl_lines.append(new_indent + '"' + feature_name + '"')
            elif ":m" in line_content:
                #feature_name = line_content.split(" ")[1]
                uvl_lines.append(new_indent + "mandatory")
                uvl_lines.append(new_indent + "\t" + '"' + feature_name + '"')
            elif ":o" in line_content:
                #feature_name = line_content.split(" ")[1]
                uvl_lines.append(new_indent + "optional")
                uvl_lines.append(new_indent + "\t" + '"' + feature_name + '"')
            elif ":g" in line_content:
                cardinality = re.search(r"\[(\d+),(\d+|\*)\]", line_content)
                if cardinality:
                    min_c, max_c = cardinality.groups()
                    group_type = "alternative" if min_c == "1" and max_c == "1" else "or"
                    uvl_lines.append(new_indent + "\t" + group_type)
            elif ":" in line_content:  # Regular features in groups
                #feature_name = line_content.split(" ")[1]
                uvl_lines.append(new_indent + "\t" + '"' + feature_name + '"')

    return id_to_name, "\n".join(uvl_lines)


def convert_constraints_to_uvl(constraints_content, id_to_name):
    uvl_constraints = ["constraints"]
    constraints_lines = constraints_content.split("\n")

    for line in constraints_lines:
        line = line.strip()
        if line.startswith("constraint_"):
            # Extraemos la parte de la expresión de la restricción después de "constraint_x:"
            expression = line.split(':', 1)[1] if ':' in line else ''
            # Reemplazamos los identificadores en la expresión
            expression = re.sub(
                r"~?(_[a-zA-Z0-9_]+)",
                lambda m: ('!' if m.group(0).startswith('~') else '') + '"' + id_to_name.get(m.group(1), "UNDEFINED") + '"',
                expression
            )
            # Convertimos la lógica de 'or' a '=>' para la sintaxis UVL de implicación
            expression = expression.replace(" or ", " => ")
            uvl_constraints.append("\t" + expression)

    return "\n".join(uvl_constraints)


def convert_constraints_to_uvl(constraints_content, id_to_name):
    uvl_constraints = ["constraints"]
    constraints_lines = constraints_content.split("\n")

    for line in constraints_lines:
        line = line.strip()
        if line.startswith("constraint_"):
            # Extraemos la parte de la expresión de la restricción después de "constraint_x:"
            expression = line.split(':', 1)[1] if ':' in line else ''
            # Procesamos cada identificador y su posible negación
            parts = expression.split(' or ')
            if len(parts) == 2:
                left_part, right_part = parts
                left_negated = '~' in left_part
                right_negated = '~' in right_part

                # Limpiar los identificadores de cualquier símbolo
                left_id = left_part.replace('~', '').strip()
                right_id = right_part.replace('~', '').strip()

                # Traducir IDs a nombres
                left_name = id_to_name.get(left_id, "UNDEFINED")
                right_name = id_to_name.get(right_id, "UNDEFINED")

                # Determinar la cadena correcta para cada lado
                left_str = f'"{left_name}"'
                right_str = f'"{right_name}"'

                # Agregar el signo de exclamación si está negado y es una relación de exclusión
                if left_negated and right_negated:
                    right_str = f'!{right_str}'
                elif right_negated:
                    left_str, right_str = right_str, left_str

                # Formar la expresión completa de restricción
                final_expression = f"{left_str} => {right_str}"
                uvl_constraints.append("\t" + final_expression)

    return "\n".join(uvl_constraints)

def generate_uvl(features, constraints):
    return features + "\n" + constraints




# Ensure the function call and usage within sxfm_to_uvl is correctly updated to pass the necessary arguments




#sxfm_content = read_uvl_file("model.xml")
#uvl_content = sxfm_to_uvl(sxfm_content)
#write_json_to_file(uvl_content, "test1.uvl")
#print(uvl_content)