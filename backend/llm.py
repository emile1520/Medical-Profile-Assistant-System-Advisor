import re
import json
from transformers import pipeline
import ollama

# ── Intent classifier (zero-shot) ─────────────────────────────────────────────
print("Loading intent classifier...")
intent_classifier = pipeline(
    "zero-shot-classification",
    model="facebook/bart-large-mnli",
)
print("Intent classifier ready!")

# ── Biomedical NER ─────────────────────────────────────────────────────────────
print("Loading NER model...")
ner_pipeline = pipeline(
    "token-classification",
    model="d4data/biomedical-ner-all",
    aggregation_strategy="simple",
)
print("NER model ready!")

# ── Supported intents (long labels improve BART accuracy) ─────────────────────
INTENT_MAP = {
    "adding or recording a medical treatment or procedure for a patient":
        "add treatment",
    "viewing or retrieving an existing patient medical profile or history":
        "view medical profile",
    "updating or modifying an existing patient record or information":
        "update patient record",
    "updating or adding allergy information or allergic reactions for a patient":
        "update allergy information",
    "creating or registering a new patient record or profile":
        "create patient record",
    "deleting or removing an existing patient record":
        "delete patient record",
}


def classify_intent(transcript: str) -> dict:
    if not transcript or not transcript.strip():
        return {"intent": "unknown", "confidence": 0.0}

    result = intent_classifier(transcript, candidate_labels=list(INTENT_MAP.keys()))
    return {
        "intent":     INTENT_MAP[result["labels"][0]],
        "confidence": round(float(result["scores"][0]), 3),
    }


def extract_entities(transcript: str) -> dict:
    if not transcript or not transcript.strip():
        return {}
    raw = ner_pipeline(transcript)
    entities = {}
    for ent in raw:
        word        = ent.get("word", "").strip()
        entity_type = ent.get("entity_group", "")
        if word and entity_type:
            entities[word] = entity_type
    return entities


# ── QWEN action + field extraction ────────────────────────────────────────────
QWEN_MODEL = "qwen3:1.7b"


# ── Deterministic keyword scoring (fast path) ────────────────────────────────
def _score_procedures_by_keywords(text: str, procedures: list) -> list:
    """
    Return [(score, key)] sorted descending. Score is the number of distinct
    keywords from a procedure's `keywords` list that appear in the text.
    """
    low = text.lower()
    scored = []
    for p in procedures:
        score = 0
        for kw in p.get("keywords", []):
            if not kw:
                continue
            if kw.lower() in low:
                score += 1
        scored.append((score, p["key"]))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return scored


# ── QWEN: detect which procedure type matches the dictation ──────────────────
def detect_procedure_type_qwen(text: str, procedures: list) -> dict:
    """
    Decide which registered procedure the dictation matches.

    Strategy:
      1. Score procedures by keyword hits. If there's a clear winner (top score
         beats runner-up by ≥ 2 and top score ≥ 1), use it — no LLM call.
      2. Otherwise, ask Qwen to break the tie / classify from scratch.

    procedures: [{key, label, service, description, keywords, examples}, ...]
    Returns: {"key": "<chosen_key or None>", "raw": "<llm raw>", "method": "keywords|qwen|fallback"}
    """
    if not text or not text.strip() or not procedures:
        return {"key": None, "raw": "", "method": "none"}

    # ── 1) Deterministic keyword scoring ─────────────────────────────────────
    scored = _score_procedures_by_keywords(text, procedures)
    print(f"[detect] keyword scores: {scored}")

    if scored:
        top_score, top_key       = scored[0]
        second_score             = scored[1][0] if len(scored) > 1 else 0
        # Clear winner: top has at least 1 hit and beats runner-up by ≥ 2
        if top_score >= 1 and (top_score - second_score) >= 2:
            print(f"[detect] keyword winner: {top_key} (score {top_score} vs {second_score})")
            return {"key": top_key, "raw": f"keywords:{top_score}", "method": "keywords"}

    # ── 2) Ambiguous or no hits → ask Qwen ────────────────────────────────────
    lines = []
    for p in procedures:
        kw = ", ".join(p.get("keywords", [])[:10]) or "—"
        lines.append(f'- "{p["key"]}" → {p["label"]} ({p.get("service", "")}). Keywords: {kw}')
    menu = "\n".join(lines)
    valid_keys = ", ".join(f'"{p["key"]}"' for p in procedures)

    prompt = f"""/no_think
You are a dental command router. Read the dentist's dictation and decide which procedure type it belongs to.

AVAILABLE PROCEDURE TYPES:
{menu}

OUTPUT RULES:
- Output ONLY a single JSON object: {{"key": "<chosen_key>"}}
- "key" must be EXACTLY one of: {valid_keys}.
- Pick the SINGLE best match. If the dictation does not clearly match any procedure, pick the closest one anyway.
- No markdown, no explanation, no thinking — just the JSON.

DICTATION:
\"\"\"{text}\"\"\"

JSON:"""

    try:
        response = ollama.chat(
            model=QWEN_MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.0},
        )
        raw = response["message"]["content"]
        print(f"[QWEN detect raw]: {raw!r}")
    except Exception as e:
        # On error, fall back to the best keyword-scored candidate if any
        if scored and scored[0][0] > 0:
            return {"key": scored[0][1], "raw": f"QWEN error, keyword fallback: {e}", "method": "fallback"}
        return {"key": None, "raw": f"QWEN error: {e}", "method": "error"}

    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", raw, flags=re.IGNORECASE)
    match = re.search(r"\{[\s\S]*?\}", cleaned)
    valid  = {p["key"] for p in procedures}

    if match:
        try:
            parsed = json.loads(match.group(0))
            key    = parsed.get("key")
            if key in valid:
                return {"key": key, "raw": raw, "method": "qwen"}
        except json.JSONDecodeError:
            pass

    # Qwen returned garbage — fall back to the highest keyword-scored candidate
    if scored and scored[0][0] > 0:
        return {"key": scored[0][1], "raw": raw, "method": "fallback"}
    return {"key": None, "raw": raw, "method": "none"}

# Maps QWEN action keys → internal CRUD intent strings
ACTION_TO_INTENT = {
    "view_record":     "view medical profile",
    "create_patient":  "create patient record",
    "update_record":   "update patient record",
    "delete_record":   "delete patient record",
    "add_treatment":   "add treatment",
    "update_allergy":  "update allergy information",
    "none":            None,
}


def _parse_json_block(text: str) -> dict:
    """Extract the first JSON object from a string. Falls back to empty result."""
    if not text:
        return {"action": "none", "fields": {}}
    # Strip possible <think>...</think> blocks Qwen3 sometimes emits
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE)
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return {"action": "none", "fields": {}}
    try:
        data = json.loads(match.group(0))
        if "action" not in data:
            data["action"] = "none"
        if "fields" not in data or not isinstance(data.get("fields"), dict):
            data["fields"] = {}
        return data
    except json.JSONDecodeError:
        return {"action": "none", "fields": {}}


def _walk_fields(node, out):
    """
    Recursively find every dict that looks like a fillable field, regardless
    of where it lives in the schema. A "field" is any dict with:
      - "label" + "fieldType", AND
      - either "key" OR "fieldKey" (Seraph's own DB stores the identifier
        as `fieldKey`, so we accept both names interchangeably and normalize
        to `key` for downstream code).
    Skips locked fields. Mutates `out` in place.

    This makes the extractor schema-shape-agnostic: callers can send a schema
    with variants/findings/results, a flat fields[] list, or any custom
    nesting — we'll find every field as long as it has the identifying keys.
    """
    if isinstance(node, dict):
        has_id = ("key" in node) or ("fieldKey" in node)
        if has_id and "label" in node and "fieldType" in node:
            if "key" not in node and "fieldKey" in node:
                node["key"] = node["fieldKey"]
            if not node.get("locked"):
                out.append(node)
            # Don't recurse into a field's own sub-objects (e.g. options)
            return
        for v in node.values():
            _walk_fields(v, out)
    elif isinstance(node, list):
        for item in node:
            _walk_fields(item, out)


def _domain_label(schema: dict) -> str:
    """
    Pull a human label for the procedure out of the schema if present, so
    the prompt can say e.g. 'composite filling dictation' instead of
    a hardcoded 'dentist's dictation'. Falls back to 'practitioner's'.
    """
    proc = schema.get("activeProcedure") if isinstance(schema, dict) else None
    if isinstance(proc, dict):
        off = proc.get("offering") or {}
        svc = proc.get("service") or {}
        for candidate in (off.get("label"), svc.get("label")):
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return "practitioner's"


def extract_procedure_fields_qwen(text: str, schema: dict) -> dict:
    """
    Schema-driven extraction. Walks the procedure schema (any shape),
    builds a constrained prompt listing every editable field + its allowed
    values, then asks QWEN to return a flat {field_key: value} JSON.

    Returns: {"fields": {...}, "raw": "<llm raw>"}
    """
    if not text or not text.strip():
        return {"fields": {}, "raw": ""}

    # Recursive walk — finds fields anywhere in the schema, not just in
    # dental-specific sections. Backward-compatible with old shape because
    # variants/findings/results.fields are still discovered.
    all_fields: list = []
    _walk_fields(schema, all_fields)

    field_lines = []
    for f in all_fields:
        key   = f.get("key")
        ftype = f.get("fieldType")
        desc  = f.get("description", "")
        opts  = f.get("options")
        unit  = f.get("unit")

        line = f"- {key} [{ftype}"
        if unit:
            line += f", unit={unit}"
        line += "]"
        if opts:
            try:
                vals = ", ".join(str(o["value"]) for o in opts if isinstance(o, dict) and "value" in o)
                if vals:
                    line += f" allowed_values=[{vals}]"
            except Exception:
                pass
        if desc:
            line += f" — {desc}"
        field_lines.append(line)

    fields_text = "\n".join(field_lines) if field_lines else "(no fillable fields found in schema)"
    domain = _domain_label(schema)

    prompt = f"""/no_think
You are a medical scribe AI. Extract field values from the {domain} dictation and map them to the structured schema below.

AVAILABLE FIELDS (key [type] allowed_values — description):
{fields_text}

OUTPUT RULES:
- Output ONLY a single valid JSON object: {{"field_key": value, ...}}
- For "select" fields, the value must be EXACTLY one of allowed_values.
- For "multi_select" / "tags", use a JSON array of strings.
- For "boolean", use true or false.
- For "number", use a numeric value (no quotes).
- For "textarea" / "tooth_picker", use a string.
- ONLY include fields that are clearly mentioned or implied in the dictation. Skip everything else.
- Do NOT invent data. If unsure, omit the field.
- No markdown, no explanation, no thinking — just the JSON.

DICTATION:
\"\"\"{text}\"\"\"

JSON:"""

    try:
        response = ollama.chat(
            model=QWEN_MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.0},
        )
        raw = response["message"]["content"]
        print(f"[QWEN procedure raw]: {raw!r}")
    except Exception as e:
        return {"fields": {}, "raw": f"QWEN error: {e}"}

    # Robust JSON parse — strip <think> blocks then find first {...} block
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", raw, flags=re.IGNORECASE)
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        return {"fields": {}, "raw": raw}
    try:
        parsed = json.loads(match.group(0))
        if not isinstance(parsed, dict):
            return {"fields": {}, "raw": raw}
        return {"fields": parsed, "raw": raw}
    except json.JSONDecodeError:
        return {"fields": {}, "raw": raw}


def extract_action_qwen(text: str) -> dict:
    """
    Calls QWEN3:4b on the approved text. Returns:
        {"action": "<key>", "fields": {...}, "intent": "<internal intent>", "raw": "<llm raw>"}
    """
    if not text or not text.strip():
        return {"action": "none", "fields": {}, "intent": None, "raw": ""}

    prompt = f"""/no_think
You are a medical command analyzer. Output a single JSON object only.

ALLOWED ACTIONS (pick exactly one):
- view_record       (looking up or showing an existing patient record)
- create_patient    (adding a new patient to the system)
- update_record     (modifying patient information)
- delete_record     (removing a patient from the system)
- add_treatment     (recording a procedure or treatment for a patient)
- update_allergy    (recording or changing a patient's allergy information)
- none              (text is not a medical command)

FIELD NAMES (include only those mentioned in the text):
- patient_name, patient_id, date_of_birth, procedure, tooth_number, allergy, clinical_notes

OUTPUT FORMAT (exact):
{{"action": "<one_of_above>", "fields": {{ ... }}}}

Examples:
Text: "Show me the record of Sara Haddad"
Output: {{"action": "view_record", "fields": {{"patient_name": "Sara Haddad"}}}}

Text: "Add tooth extraction for tooth 24 for patient John Doe"
Output: {{"action": "add_treatment", "fields": {{"patient_name": "John Doe", "procedure": "tooth extraction", "tooth_number": "24"}}}}

Text: "Hello how are you"
Output: {{"action": "none", "fields": {{}}}}

Now analyze this text and return ONLY the JSON object:
Text: "{text}"
Output:"""

    try:
        response = ollama.chat(
            model=QWEN_MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.0},
        )
        raw = response["message"]["content"]
        print(f"[QWEN raw output]: {raw!r}")
    except Exception as e:
        return {"action": "none", "fields": {}, "intent": None,
                "raw": f"QWEN error: {e}"}

    parsed = _parse_json_block(raw)
    action = parsed.get("action", "none")
    intent = ACTION_TO_INTENT.get(action)

    return {
        "action": action,
        "fields": parsed.get("fields", {}),
        "intent": intent,
        "raw":    raw,
    }
