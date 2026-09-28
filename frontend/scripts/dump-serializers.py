"""Dumps DRF serializer field contracts as JSON.

There is no OpenAPI schema in this project (no drf-spectacular in
requirements.txt), so the TypeScript contracts in src/types/api are written by
hand. Reading the serializer source to do that is slow and easy to get subtly
wrong; asking DRF what fields it actually produces is neither.

Run from backend/ with the project venv:
    .venv/Scripts/python.exe manage.py shell \
        -c "exec(open('../frontend/scripts/dump-serializers.py').read())"

Optionally set MODULES to a comma-separated list of app labels to limit output.
"""

import importlib
import inspect
import json
import os

from rest_framework import serializers

MODULES = os.environ.get("MODULES", "sales,purchases,items,inventory,accounting,projects,banking,documents,ai,automation,reports")


def describe_field(field) -> dict:
    info = {
        "type": type(field).__name__,
        "required": bool(getattr(field, "required", False)),
        "read_only": bool(getattr(field, "read_only", False)),
        "allow_null": bool(getattr(field, "allow_null", False)),
    }

    if isinstance(field, serializers.DecimalField):
        # Money and quantities: DRF serializes these as STRINGS by default
        # (COERCE_DECIMAL_TO_STRING), which is why the TS type is a string.
        info["decimal"] = {"max_digits": field.max_digits, "decimal_places": field.decimal_places}
    if isinstance(field, serializers.ChoiceField) and not isinstance(field, serializers.MultipleChoiceField):
        info["choices"] = list(field.choices.keys())
    if isinstance(field, serializers.ListSerializer):
        info["many"] = True
        info["child"] = type(field.child).__name__
    elif isinstance(field, serializers.BaseSerializer):
        info["nested"] = type(field).__name__
    if isinstance(field, serializers.SerializerMethodField):
        info["method"] = True

    return info


def describe(serializer_class) -> dict | None:
    try:
        instance = serializer_class()
        fields = instance.get_fields()
    except Exception as exc:  # a serializer needing request context
        return {"error": f"{type(exc).__name__}: {exc}"}

    return {name: describe_field(field) for name, field in fields.items()}


out: dict[str, dict] = {}

for label in MODULES.split(","):
    label = label.strip()
    for module_path in (f"{label}.api.serializers", f"{label}.serializers"):
        try:
            module = importlib.import_module(module_path)
        except ModuleNotFoundError:
            continue

        for name, obj in inspect.getmembers(module, inspect.isclass):
            if not issubclass(obj, serializers.BaseSerializer):
                continue
            # Only classes DEFINED here, not imported into the namespace.
            if obj.__module__ != module_path:
                continue
            described = describe(obj)
            if described:
                out[f"{label}.{name}"] = described

print(json.dumps(out, indent=1, sort_keys=True))
