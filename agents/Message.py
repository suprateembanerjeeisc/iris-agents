import re
from typing import Any
from pydantic import BaseModel
from .utils import create_class
from .models import MessageType


class Message:

    def __init__(self, name: str, model: type[BaseModel], message_type: str):
        MessageType(message_type)
        self.name = name
        self.type = message_type
        self.build(model)

    def sanitize(self, json_name: str) -> str:
        parts = re.split(r'[^A-Za-z0-9]+', json_name)
        parts = [part for part in parts if part]
        if not parts:
            raise ValueError(f"Invalid property name: {json_name!r}")
        name = ''.join(part[:1].upper() + part[1:] for part in parts)
        if not name[0].isalpha():
            name = "P" + name
        return name

    def build(self, model: type[BaseModel]) -> None:
        schema = model.model_json_schema()
        defs = schema.get("$defs", {})

        scalar_type_map = {
            "string": '%String(MAXLEN = "")',
            "integer": "%Integer",
            "number": "%Double",
            "boolean": "%Boolean",
        }

        built: set[str] = set()
        pending: list[tuple[str, dict[str, Any], bool]] = [(self.name, schema, True)]

        while pending:
            class_name, class_schema, top_level = pending.pop()

            if class_name in built:
                continue
            built.add(class_name)

            properties = class_schema.get("properties", {})
            if not isinstance(properties, dict):
                raise ValueError(f"Schema for {class_name} is not an object schema")

            parent = f'Ens.{self.type}, %JSON.Adaptor' if top_level else '%SerialObject, %JSON.Adaptor'
            cls_name = f'Agents.Message.{class_name}'
            lines: list[str] = [f'Class {cls_name} Extends ({parent})', '{']

            for prop_name, prop_schema in properties.items():

                if "anyOf" in prop_schema:
                    non_null = [x for x in prop_schema["anyOf"] if x.get("type") != "null"]
                    if len(non_null) == 1:
                        prop_schema = non_null[0]

                iris_name = self.sanitize(prop_name)

                if "$ref" in prop_schema:
                    ref = prop_schema["$ref"]
                    if not ref.startswith("#/$defs/"):
                        raise ValueError(f"Unsupported schema ref: {ref}")
                    nested_name = ref.split("/")[-1]

                    pending.append((nested_name, defs[nested_name], False))
                    lines.append(
                        f'Property {iris_name} As Agents.Message.{nested_name}(%JSONFIELDNAME = "{prop_name}");'
                    )
                    continue

                prop_type = prop_schema.get("type")

                if prop_type == "object":
                    inline_class_name = f"{class_name}{iris_name}"
                    pending.append((inline_class_name, prop_schema, False))
                    lines.append(
                        f'Property {iris_name} As Agents.Message.{inline_class_name}(%JSONFIELDNAME = "{prop_name}");'
                    )
                    continue

                if prop_type == "array":
                    items = prop_schema.get("items", {})

                    if "anyOf" in items:
                        non_null = [x for x in items["anyOf"] if x.get("type") != "null"]
                        if len(non_null) == 1:
                            items = non_null[0]

                    if "$ref" in items:
                        ref = items["$ref"]
                        if not ref.startswith("#/$defs/"):
                            raise ValueError(f"Unsupported schema ref: {ref}")
                        nested_name = ref.split("/")[-1]

                        pending.append((nested_name, defs[nested_name], False))
                        lines.append(
                            f'Property {iris_name} As list Of Agents.Message.{nested_name}(%JSONFIELDNAME = "{prop_name}");'
                        )
                        continue

                    item_type = items.get("type")

                    if item_type == "object":
                        inline_class_name = f"{class_name}{iris_name}Item"
                        pending.append((inline_class_name, items, False))
                        lines.append(
                            f'Property {iris_name} As list Of Agents.Message.{inline_class_name}(%JSONFIELDNAME = "{prop_name}");'
                        )
                        continue

                    scalar_iris = scalar_type_map.get(item_type, '%String(MAXLEN = "")')
                    if "(" in scalar_iris and scalar_iris.endswith(")"):
                        scalar_expr = scalar_iris[:-1] + f', %JSONFIELDNAME = "{prop_name}")'
                    else:
                        scalar_expr = f'{scalar_iris}(%JSONFIELDNAME = "{prop_name}")'

                    lines.append(f'Property {iris_name} As list Of {scalar_expr};')
                    continue

                scalar_iris = scalar_type_map.get(prop_type, '%String(MAXLEN = "")')
                if "(" in scalar_iris and scalar_iris.endswith(")"):
                    scalar_expr = scalar_iris[:-1] + f', %JSONFIELDNAME = "{prop_name}")'
                else:
                    scalar_expr = f'{scalar_iris}(%JSONFIELDNAME = "{prop_name}")'

                lines.append(f'Property {iris_name} As {scalar_expr};')

            lines.append('}')
            create_class(cls_name, '\n'.join(lines))