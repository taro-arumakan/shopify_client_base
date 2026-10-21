"""Check every GraphQL operation in the given repos against a newer Admin API version.

Pulls the Admin GraphQL schema for two API versions from a live store (an
introspection query, read only), finds the GraphQL strings in the Python
sources, and reports per operation:

  BREAKS      valid on the current version, invalid on the target version
  DEPRECATED  uses a field, argument or enum value deprecated on the target
  INVALID     invalid on both versions (a bug, or a string this script
              could not reconstruct)

Interpolated parts of a string (f-string fields, %-formatting, string.Template)
are replaced by a placeholder name, and validation errors that mention it are
ignored, so the rest of such an operation is still checked.

Usage, from a directory whose .env holds `<shop>-ACCESS_TOKEN`:

    uv run --with graphql-core python scripts/check_api_version.py \\
        --shop blossomhcompany --to 2026-10 \\
        src ../catal_ec_management ../sniarti_ec_management

Schemas are cached in --schema-dir, so a rerun needs no network.
"""

import argparse
import ast
import json
import os
import pathlib
import re
import sys

import requests
from dotenv import find_dotenv, load_dotenv
from graphql import (
    GraphQLSchema,
    TypeInfo,
    TypeInfoVisitor,
    Visitor,
    build_client_schema,
    get_introspection_query,
    get_named_type,
    parse,
    validate,
    visit,
)
from graphql.type import GraphQLEnumType

SKIP_DIRS = {".venv", "deprecated", ".git", "node_modules"}
PLACEHOLDER = "PLACEHOLDER__"


def fetch_schema(shop, token, version, schema_dir: pathlib.Path) -> GraphQLSchema:
    path = schema_dir / f"admin_{version}.json"
    if not path.exists():
        res = requests.post(
            f"https://{shop}.myshopify.com/admin/api/{version}/graphql.json",
            headers={"X-Shopify-Access-Token": token},
            json={"query": get_introspection_query(input_value_deprecation=True)},
            timeout=120,
        )
        res.raise_for_status()
        body = res.json()
        if "errors" in body:
            raise RuntimeError(f"introspection failed on {version}: {body['errors']}")
        schema_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body["data"]))
    # Shopify's schema breaks a few interface-deprecation rules that graphql-core
    # 3.3 enforces before validating, so take it as valid.
    return build_client_schema(json.loads(path.read_text()), assume_valid=True)


def to_graphql(source):
    """Turn a Python string holding a GraphQL operation into parseable GraphQL.

    Formatting placeholders become a bare name, which keeps the document
    parseable in most positions.
    """
    source = re.sub(r"%\(\w+\)s|%[sdr]", PLACEHOLDER, source)
    if "$$" in source:  # string.Template
        source = re.sub(r"\$\w+", PLACEHOLDER, source.replace("$$", "\0")).replace(
            "\0", "$"
        )
    return source


def find_operations(roots):
    for root in roots:
        for path in pathlib.Path(root).rglob("*.py"):
            if SKIP_DIRS.intersection(path.parts):
                continue
            try:
                tree = ast.parse(path.read_text())
            except SyntaxError:
                continue
            parents = {
                child: node
                for node in ast.walk(tree)
                for child in ast.iter_child_nodes(node)
            }

            def enclosing_function(node):
                while node in parents:
                    node = parents[node]
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        return node.name
                return "<module>"

            for node in ast.walk(tree):
                if isinstance(node, ast.JoinedStr):
                    text = "".join(
                        v.value if isinstance(v, ast.Constant) else PLACEHOLDER
                        for v in node.values
                    )
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if isinstance(parents.get(node), ast.JoinedStr):
                        continue
                    text = node.value
                else:
                    continue
                text = text.strip()
                if not re.match(r"(query|mutation)\b", text) or "{" not in text:
                    continue
                yield {
                    "file": str(path),
                    "line": node.lineno,
                    "function": enclosing_function(node),
                    "graphql": to_graphql(text),
                }


def deprecations(schema, document):
    found = []
    type_info = TypeInfo(schema)

    class Collect(Visitor):
        def enter_field(self, node, *_):
            field = type_info.get_field_def()
            parent = type_info.get_parent_type()
            if field and field.deprecation_reason:
                found.append(
                    f"{parent.name}.{node.name.value}: {field.deprecation_reason}"
                )

        def enter_argument(self, node, *_):
            arg = type_info.get_argument()
            if arg and arg.deprecation_reason:
                found.append(f"argument {node.name.value}: {arg.deprecation_reason}")

        def enter_object_field(self, node, *_):
            input_type = get_named_type(type_info.get_parent_input_type())
            if input_type and hasattr(input_type, "fields"):
                input_field = input_type.fields.get(node.name.value)
                if input_field and input_field.deprecation_reason:
                    found.append(
                        f"{input_type.name}.{node.name.value}: {input_field.deprecation_reason}"
                    )

        def enter_enum_value(self, node, *_):
            enum = get_named_type(type_info.get_input_type())
            if isinstance(enum, GraphQLEnumType):
                value = enum.values.get(node.value)
                if value and value.deprecation_reason:
                    found.append(
                        f"{enum.name}.{node.value}: {value.deprecation_reason}"
                    )

    visit(document, TypeInfoVisitor(type_info, Collect()))
    return found


def variable_input_deprecations(schema, document):
    """Deprecated input fields can't be seen when the input arrives as a
    variable, so list the deprecated fields of every input type a variable
    declares. They need a manual look at what the Python code passes in."""
    found = []
    for definition in document.definitions:
        for var in getattr(definition, "variable_definitions", None) or []:
            type_name = var.type
            while hasattr(type_name, "type"):
                type_name = type_name.type
            input_type = schema.get_type(type_name.name.value)
            if input_type and hasattr(input_type, "fields"):
                deprecated = [
                    name
                    for name, f in input_type.fields.items()
                    if getattr(f, "deprecation_reason", None)
                ]
                if deprecated:
                    found.append(
                        f"${var.variable.name.value}: {input_type.name} has deprecated "
                        f"fields {', '.join(deprecated)}; check what the code passes"
                    )
    return found


def real_errors(errors):
    return [e for e in errors if PLACEHOLDER not in e.message]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--shop", required=True)
    parser.add_argument("--from", dest="from_version", default="2026-01")
    parser.add_argument("--to", dest="to_version", required=True)
    parser.add_argument("--schema-dir", default=".api_schemas")
    args = parser.parse_args()

    load_dotenv(find_dotenv(usecwd=True))
    token = os.environ[f"{args.shop}-ACCESS_TOKEN"]
    schema_dir = pathlib.Path(args.schema_dir)
    current = fetch_schema(args.shop, token, args.from_version, schema_dir)
    target = fetch_schema(args.shop, token, args.to_version, schema_dir)

    counts = {"ok": 0, "BREAKS": 0, "DEPRECATED": 0, "INVALID": 0, "UNPARSED": 0}
    for op in find_operations(args.paths):
        where = f"{op['file']}:{op['line']} {op['function']}"
        try:
            document = parse(op["graphql"])
        except Exception as e:
            counts["UNPARSED"] += 1
            print(f"UNPARSED   {where}\n    {str(e).splitlines()[0]}")
            continue
        errors_now = real_errors(validate(current, document))
        errors_target = real_errors(validate(target, document))
        if errors_target and not errors_now:
            counts["BREAKS"] += 1
            print(f"BREAKS     {where}")
            for e in errors_target:
                print(f"    {e.message}")
        elif errors_target:
            counts["INVALID"] += 1
            print(f"INVALID    {where}")
            for e in errors_target:
                print(f"    {e.message}")
        notes = deprecations(target, document) + variable_input_deprecations(
            target, document
        )
        if notes:
            counts["DEPRECATED"] += 1
            print(f"DEPRECATED {where}")
            for n in dict.fromkeys(notes):
                print(f"    {n}")
        if not errors_target and not notes:
            counts["ok"] += 1
    print(
        f"\n{args.from_version} -> {args.to_version}: "
        + ", ".join(f"{k} {v}" for k, v in counts.items())
    )
    return 1 if counts["BREAKS"] else 0


if __name__ == "__main__":
    sys.exit(main())
