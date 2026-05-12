import json
import copy
import os

_SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "procedure_schema.json")
with open(_SCHEMA_PATH, "r", encoding="utf-8") as _f:
    _SAMPLE_PROCEDURE = json.load(_f)


def get_sample_procedure() -> dict:
    """Return a fresh deep copy of the sample procedure schema."""
    return copy.deepcopy(_SAMPLE_PROCEDURE)


def flatten_fields(schema: dict) -> list:
    """
    Walk the schema and return a flat list of all editable fields with section labels.
    Each entry: {section, key, label, fieldType, options, required, locked, value, ...}
    """
    out = []
    proc = schema.get("activeProcedure", {})

    for v in proc.get("variants", []):
        out.append({**v, "section": "variants"})
    for f in proc.get("findings", []):
        out.append({**f, "section": "findings"})
    for r in proc.get("results", {}).get("fields", []):
        out.append({**r, "section": "results"})
    return out


def merge_qwen_values(schema: dict, qwen_values: dict) -> dict:
    """
    Apply QWEN-extracted values into the schema (in place on a copy).
    Skips locked fields. Marks each updated field with `filled_by_ai: True`.
    """
    proc = schema.get("activeProcedure", {})

    def maybe_update(field):
        key = field.get("key")
        if not key or field.get("locked"):
            return
        if key in qwen_values and qwen_values[key] is not None:
            field["value"] = qwen_values[key]
            field["filled_by_ai"] = True

    for f in proc.get("variants", []):
        maybe_update(f)
    for f in proc.get("findings", []):
        maybe_update(f)
    for f in proc.get("results", {}).get("fields", []):
        maybe_update(f)

    return schema


def compute_missing_required(schema: dict) -> list:
    """Return list of {key, label, section} for required fields with empty value."""
    missing = []
    for f in flatten_fields(schema):
        if not f.get("required"):
            continue
        if f.get("locked"):
            continue
        v = f.get("value")
        is_empty = (
            v is None
            or v == ""
            or (isinstance(v, list) and len(v) == 0)
        )
        if is_empty:
            missing.append({
                "key":     f["key"],
                "label":   f["label"],
                "section": f["section"],
            })
    return missing


def validate_required_values(field_values: dict) -> list:
    """
    Given a flat {key: value} dict (from frontend on approve), return the list of
    required keys that are still empty. Used for approval-time validation.
    """
    schema = get_sample_procedure()
    missing = []
    for f in flatten_fields(schema):
        if not f.get("required") or f.get("locked"):
            continue
        v = field_values.get(f["key"], None)
        is_empty = (
            v is None
            or v == ""
            or (isinstance(v, list) and len(v) == 0)
        )
        if is_empty:
            missing.append({"key": f["key"], "label": f["label"], "section": f["section"]})
    return missing
