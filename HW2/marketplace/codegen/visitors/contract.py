from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi_code_generator.parser import OpenAPIParser, Operation, snakecase

HTTP_METHODS = {"get", "put", "post", "delete", "patch", "head", "options", "trace"}


def _raw(value: Any) -> str:
    return getattr(value, "raw", str(value))


def _pascal(tag: str) -> str:
    return "".join(part.capitalize() for part in re.split(r"[^0-9a-zA-Z]+", tag) if part)


def _snake(tag: str) -> str:
    return "_".join(part.lower() for part in re.split(r"[^0-9a-zA-Z]+", tag) if part)


def _raw_operations(spec: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for path, item in (spec.get("paths") or {}).items():
        for method, operation in item.items():
            if method in HTTP_METHODS:
                result[(path, method)] = operation
    return result


def _operation_context(operation: Operation, raw: dict[str, Any], global_security: Any) -> dict[str, Any]:
    security = raw.get("security", global_security) or []
    secured = any(requirement for requirement in security)
    arguments = operation._merged_arguments
    return {
        "function_name": operation.function_name,
        "method": operation.method,
        "path": _raw(operation.path),
        "summary": " ".join(_raw(operation.summary or "").split()),
        "status_code": operation.status_code or 200,
        "response": operation.response or "None",
        "secured": secured,
        "roles": list(raw.get("x-roles") or []),
        "route_params": [argument.snakecase for argument in arguments],
        "interface_params": [f"{snakecase(argument.name)}: {argument.resolved_type_hint}" for argument in arguments],
        "names": [snakecase(argument.name) for argument in arguments],
    }


def get_api_groups(parser: OpenAPIParser, model_path: Path) -> dict[str, object]:
    spec = parser.raw_obj if isinstance(parser.raw_obj, dict) else {}
    raw_operations = _raw_operations(spec)
    global_security = spec.get("security")

    groups: dict[str, dict[str, Any]] = {}
    for operation in sorted(parser.operations.values(), key=lambda op: (_raw(op.path), op.method)):
        tag = _raw(operation.tags[0]) if operation.tags else "default"
        raw = raw_operations[(_raw(operation.path), operation.method)]
        group = groups.setdefault(
            tag,
            {
                "tag": tag,
                "interface": f"{_pascal(tag)}Api",
                "factory": f"create_{_snake(tag)}_router",
                "operations": [],
            },
        )
        group["operations"].append(_operation_context(operation, raw, global_security))

    api_groups: list[dict[str, Any]] = sorted(groups.values(), key=lambda g: g["tag"])
    for group in api_groups:
        group["secured"] = any(op["secured"] for op in group["operations"])
    return {"api_groups": api_groups}


visit = get_api_groups
