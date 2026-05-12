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


def extract_procedure_fields_qwen(text: str, schema: dict) -> dict:
    """
    Schema-driven extraction. Walks the procedure schema, builds a constrained
    prompt listing every editable field + its allowed values, then asks QWEN
    to return a flat {field_key: value} JSON.

    Returns: {"fields": {...}, "raw": "<llm raw>"}
    """
    if not text or not text.strip():
        return {"fields": {}, "raw": ""}

    # Flatten schema → list editable, non-locked fields with descriptions
    proc = schema.get("activeProcedure", {})
    sections = [
        ("variants", proc.get("variants", [])),
        ("findings", proc.get("findings", [])),
        ("results",  proc.get("results", {}).get("fields", [])),
    ]

    field_lines = []
    for section_name, items in sections:
        for f in items:
            if f.get("locked"):
                continue
            key      = f.get("key")
            ftype    = f.get("fieldType")
            desc     = f.get("description", "")
            opts     = f.get("options")
            unit     = f.get("unit")

            line = f"- {key} [{ftype}"
            if unit:
                line += f", unit={unit}"
            line += "]"
            if opts:
                vals = ", ".join(o["value"] for o in opts)
                line += f" allowed_values=[{vals}]"
            if desc:
                line += f" — {desc}"
            field_lines.append(line)

    fields_text = "\n".join(field_lines)

    prompt = f"""/no_think
You are a medical scribe AI. Extract field values from the dentist's dictation and map them to the structured schema below.

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
