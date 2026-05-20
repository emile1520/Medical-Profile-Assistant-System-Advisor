import json
import copy
import os

_BASE_DIR    = os.path.dirname(__file__)
_SCHEMAS_DIR = os.path.join(_BASE_DIR, "schemas")
_LEGACY_FILE = os.path.join(_BASE_DIR, "procedure_schema.json")


# ── Registry: load every JSON under schemas/ on import ────────────────────────
def _load_registry() -> dict:
    registry = {}
    if os.path.isdir(_SCHEMAS_DIR):
        for fname in sorted(os.listdir(_SCHEMAS_DIR)):
            if not fname.endswith(".json"):
                continue
            key = os.path.splitext(fname)[0]
            try:
                with open(os.path.join(_SCHEMAS_DIR, fname), "r", encoding="utf-8") as f:
                    registry[key] = json.load(f)
            except Exception as e:
                print(f"[procedure_schema] Failed to load {fname}: {e}")
    # Fallback to the legacy single-file schema if nothing was found
    if not registry and os.path.isfile(_LEGACY_FILE):
        try:
            with open(_LEGACY_FILE, "r", encoding="utf-8") as f:
                registry["default"] = json.load(f)
        except Exception as e:
            print(f"[procedure_schema] Failed to load legacy schema: {e}")
    return registry


_REGISTRY = _load_registry()
print(f"[procedure_schema] Loaded {len(_REGISTRY)} procedure schema(s): {list(_REGISTRY)}")


# ── Lookup helpers ────────────────────────────────────────────────────────────
def list_procedures() -> list:
    """Return [{key, label, service, description, keywords, examples}, ...] for detection."""
    out = []
    for key, schema in _REGISTRY.items():
        proc     = schema.get("activeProcedure", {})
        offering = proc.get("offering", {})
        service  = proc.get("service", {})
        hints    = proc.get("detectionHints", {})
        out.append({
            "key":         key,
            "label":       offering.get("label", key),
            "service":     service.get("label", ""),
            "description": offering.get("description", ""),
            "keywords":    hints.get("keywords", []),
            "examples":    hints.get("examples", []),
        })
    return out


def get_procedure_by_key(key: str) -> dict:
    """Return a fresh deep copy of the named procedure schema. Falls back to first available."""
    if key and key in _REGISTRY:
        return copy.deepcopy(_REGISTRY[key])
    if _REGISTRY:
        first = next(iter(_REGISTRY))
        return copy.deepcopy(_REGISTRY[first])
    return {"activeProcedure": {}}


def get_sample_procedure() -> dict:
    """Backward-compat: returns root_canal if present, else the first registered procedure."""
    if "root_canal" in _REGISTRY:
        return copy.deepcopy(_REGISTRY["root_canal"])
    if _REGISTRY:
        first = next(iter(_REGISTRY))
        return copy.deepcopy(_REGISTRY[first])
    return {"activeProcedure": {}}


# ── Schema operations (procedure-agnostic) ────────────────────────────────────
def flatten_fields(schema: dict) -> list:
    """
    Walk the schema and return a flat list of all editable fields with section labels.
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


def validate_required_values(field_values: dict, procedure_key: str = None) -> list:
    """
    Given a flat {key: value} dict (from frontend on approve), return required keys
    still empty. Uses the appropriate schema if procedure_key is provided.
    """
    schema  = get_procedure_by_key(procedure_key) if procedure_key else get_sample_procedure()
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
