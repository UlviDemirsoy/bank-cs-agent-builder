"""Static, brief-agnostic checks for a Freya workflow.

Usage: python builder/preflight.py WORKFLOW.json [OPENAPI.json]
Exit 1 means the coding agent should fix the workflow before dry-run/save.
"""
import json
import pathlib
import re
import sys


def tool_name(tool):
    return (tool.get("function") or {}).get("name", "") if isinstance(tool, dict) else ""


def walk(value, path="$"):
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from walk(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, f"{path}[{index}]")


def code_strings(node):
    for action in node.get("pre_actions") or []:
        function = action.get("function") or {}
        if function.get("name") != "code_exec":
            continue
        properties = ((function.get("parameters") or {}).get("properties") or {})
        code = (properties.get("code") or {}).get("default")
        if isinstance(code, str):
            yield code
    for tool in node.get("tools") or []:
        for key in ("pre_process_code", "post_process_code"):
            code = tool.get(key)
            if isinstance(code, str):
                yield code


def node_is_cyclic(name, graph):
    stack = list(graph.get(name, ()))
    seen = set()
    while stack:
        current = stack.pop()
        if current == name:
            return True
        if current in seen:
            continue
        seen.add(current)
        stack.extend(graph.get(current, ()))
    return False


def check(workflow):
    errors, warnings = [], []
    nodes = workflow.get("nodes")
    if not isinstance(nodes, dict) or not nodes:
        return ["workflow.nodes must be a non-empty object"], warnings
    initial = workflow.get("initial_node")
    if initial not in nodes:
        errors.append("initial_node must name an existing node")
    if "agent_config" in workflow:
        errors.append("omit agent_config; Freya supplies it")
    for path, value in walk(workflow):
        if value is None:
            errors.append(f"{path} is null")

    graph = {name: [] for name in nodes}
    for name, node in nodes.items():
        if not isinstance(node, dict):
            errors.append(f"node {name} is not an object")
            continue
        if node.get("name") not in (None, name):
            errors.append(f"node key {name} disagrees with node.name {node.get('name')}")
        for transition in node.get("transitions") or []:
            target = transition.get("target_node")
            if target and target != "__previous__" and target not in nodes:
                errors.append(f"{name} transitions to missing node {target}")
            elif target in nodes:
                graph[name].append(target)
        node_type = node.get("node_type")
        tools = node.get("tools") or []
        actions = node.get("pre_actions") or []
        if node_type == "conversation" and actions:
            errors.append(f"{name}: conversation nodes cannot keep pre_actions")
        if node_type in ("api_request", "code"):
            if tools:
                errors.append(f"{name}: {node_type} nodes cannot keep tools")
            if not actions:
                errors.append(f"{name}: {node_type} node has no pre_actions")
            if not node.get("passthrough"):
                warnings.append(f"{name}: action node is not passthrough")
        for code in code_strings(node):
            if "{{" in code or "}}" in code:
                errors.append(f"{name}: code contains template braces; read vars instead")

    for name, node in nodes.items():
        on_enter = node.get("on_enter") or {}
        if (
            on_enter.get("type") == "tts"
            and node_is_cyclic(name, graph)
            and not node.get("on_enter_first_entry_only")
        ):
            errors.append(f"{name}: cyclic TTS node must set on_enter_first_entry_only")

    lifecycle = workflow.get("tools") or []
    if not any("end_of_call" in (tool.get("config") or {}).get("alwaysRunsAt", []) for tool in lifecycle):
        warnings.append("no workflow-level end_of_call lifecycle tool")
    for tool in lifecycle:
        if not (tool.get("config") or {}).get("alwaysRunsAt"):
            errors.append(f"workflow tool {tool_name(tool)} has no alwaysRunsAt and will never run")

    workflow_text = json.dumps(workflow).lower()
    has_calendar_policy = any(
        phrase in workflow_text
        for phrase in ("business day", "holiday", "date normalization", "date validation")
    )
    has_date_write = any(
        "date" in ((((tool.get("function") or {}).get("parameters") or {}).get("properties") or {}).get("body") or {}).get("properties", {})
        for tool in api_tools(workflow)
    )
    has_callable_date_guard = any(
        node.get("node_type") == "conversation"
        and any(
            (tool.get("function") or {}).get("handler") == "__function__:code_exec"
            and "date" in (
                ((tool.get("function") or {}).get("name") or "")
                + " "
                + ((tool.get("function") or {}).get("description") or "")
            ).lower()
            for tool in node.get("tools") or []
        )
        for node in nodes.values()
    )
    if has_calendar_policy and has_date_write and not has_callable_date_guard:
        warnings.append(
            "calendar policy writes a date but has no model-callable deterministic date guard; "
            "customer-proposed dates may be calculated by the dialogue model"
        )

    produced = set()
    for node in nodes.values():
        for code in code_strings(node):
            produced.update(re.findall(r"""vars\s*\[\s*['"]([A-Za-z_][A-Za-z0-9_]*)['"]\s*\]\s*=""", code))
        produced.update(
            item.get("variable")
            for item in node.get("response_mapping") or []
            if isinstance(item, dict) and item.get("variable")
        )
        produced.update(
            item.get("name")
            for item in node.get("extraction_schema") or []
            if isinstance(item, dict) and item.get("name")
        )
    spoken = set()
    for node in nodes.values():
        for value in (
            node.get("task_message"),
            (node.get("on_enter") or {}).get("text"),
            node.get("spoken_message"),
        ):
            if isinstance(value, str):
                spoken.update(re.findall(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}", value))
    for variable in sorted(spoken - produced):
        warnings.append(
            f"spoken placeholder {variable!r} has no workflow producer; confirm it is an explicit call input"
        )
    return errors, warnings


def resolve_schema(spec, schema):
    seen = set()
    while isinstance(schema, dict) and schema.get("$ref"):
        ref = schema["$ref"]
        if ref in seen or not ref.startswith("#/"):
            break
        seen.add(ref)
        value = spec
        for part in ref[2:].split("/"):
            value = value.get(part, {})
        schema = value
    return schema if isinstance(schema, dict) else {}


def openapi_operations(spec):
    server = ((spec.get("servers") or [{}])[0].get("url") or "").rstrip("/")
    operations = {}
    for path, methods in (spec.get("paths") or {}).items():
        for method, operation in methods.items():
            if method.lower() not in ("get", "post", "put", "patch", "delete"):
                continue
            content = ((operation.get("requestBody") or {}).get("content") or {})
            media = content.get("application/json") or {}
            schema = resolve_schema(spec, media.get("schema") or {})
            properties = {
                name: resolve_schema(spec, field)
                for name, field in (schema.get("properties") or {}).items()
            }
            operations[server + path] = {
                "method": method.upper(),
                "properties": properties,
                "required": set(schema.get("required") or []) - {"customer_id"},
            }
    return operations


def api_tools(workflow):
    nodes = (workflow.get("nodes") or {}).values()
    collections = [workflow.get("tools") or []]
    for node in nodes:
        collections.extend((node.get("tools") or [], node.get("pre_actions") or []))
    for tools in collections:
        for tool in tools:
            function = tool.get("function") or {}
            if function.get("handler") == "__function__:api_call":
                yield tool


def check_openapi(workflow, spec):
    errors = []
    operations = openapi_operations(spec)
    for tool in api_tools(workflow):
        function = tool.get("function") or {}
        parameters = (function.get("parameters") or {}).get("properties") or {}
        url = (parameters.get("url") or {}).get("default")
        name = function.get("name") or "<unnamed>"
        if url not in operations:
            errors.append(f"{name}: URL is not an operation in this run's OpenAPI: {url!r}")
            continue
        expected = operations[url]
        method = (parameters.get("method") or {}).get("default")
        if method != expected["method"]:
            errors.append(f"{name}: method {method!r} does not match OpenAPI {expected['method']}")
        body = parameters.get("body") or {}
        actual_properties = body.get("properties") or {}
        expected_names = set(expected["properties"])
        actual_names = set(actual_properties)
        must_present = set(expected["required"])
        if "customer_id" in expected_names:
            must_present.add("customer_id")
        missing = sorted(must_present - actual_names)
        extra = sorted(actual_names - expected_names)
        if missing or extra:
            errors.append(
                f"{name}: body fields violate OpenAPI; missing required={missing}, unknown={extra}"
            )
        actual_required = set(body.get("required") or [])
        if not actual_required <= expected["required"]:
            errors.append(
                f"{name}: required list contains non-required OpenAPI fields: "
                f"{sorted(actual_required - expected['required'])}"
            )
        for field in expected_names & actual_names:
            wanted = expected["properties"][field]
            actual = actual_properties[field]
            if wanted.get("type") and actual.get("type") != wanted.get("type"):
                errors.append(
                    f"{name}.{field}: type {actual.get('type')!r} does not match OpenAPI {wanted.get('type')!r}"
                )
            if wanted.get("enum") and not set(actual.get("enum") or []) <= set(wanted["enum"]):
                errors.append(f"{name}.{field}: enum contains a value outside OpenAPI")
            if (
                wanted.get("enum")
                and isinstance(actual.get("default"), str)
                and not actual["default"].startswith("{{")
                and actual["default"] not in wanted["enum"]
            ):
                errors.append(f"{name}.{field}: fixed default is outside the OpenAPI enum")
            if field == "customer_id" and actual.get("default") != "{{customer_id}}":
                errors.append(
                    f"{name}.{field}: customer_id must be baked from '{{{{customer_id}}}}'"
                )
    return errors


def check_tool_argument_keys(workflow):
    """Catch code reading API field names when Freya stores a placeholder-named argument."""
    errors = []
    all_code = "\n".join(
        code
        for node in (workflow.get("nodes") or {}).values()
        for code in code_strings(node)
    )
    for tool in api_tools(workflow):
        function = tool.get("function") or {}
        name = function.get("name") or ""
        properties = (function.get("parameters") or {}).get("properties") or {}
        body_properties = (properties.get("body") or {}).get("properties") or {}
        for field, schema in body_properties.items():
            default = schema.get("default") if isinstance(schema, dict) else None
            match = re.fullmatch(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}", default or "")
            if not match or match.group(1) == field:
                continue
            placeholder = match.group(1)
            wrong_key = f"{name}_args.{field}"
            right_key = f"{name}_args.{placeholder}"
            if wrong_key in all_code and right_key not in all_code:
                errors.append(
                    f"{name}: code reads {wrong_key!r}, but field {field!r} uses placeholder "
                    f"{placeholder!r}; read {right_key!r} or handle both explicitly"
                )
    return errors


def main():
    if len(sys.argv) not in (2, 3):
        raise SystemExit(__doc__)
    path = pathlib.Path(sys.argv[1])
    workflow = json.loads(path.read_text(encoding="utf-8"))
    errors, warnings = check(workflow)
    errors.extend(check_tool_argument_keys(workflow))
    if len(sys.argv) == 3:
        spec = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
        errors.extend(check_openapi(workflow, spec))
    for message in warnings:
        print(f"WARN {message}")
    for message in errors:
        print(f"ERROR {message}")
    print(f"preflight: {len(errors)} error(s), {len(warnings)} warning(s)")
    raise SystemExit(1 if errors else 0)


if __name__ == "__main__":
    main()
