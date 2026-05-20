from fastapi import FastAPI, File, UploadFile, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Any, Dict, Optional
import whisper
import os
import re
import json
import time
from datetime import datetime
from database import (
    init_db, insert_recording, get_all_recordings,
    get_recordings_by_user, get_recordings_by_date,
)
from llm import extract_procedure_fields_qwen, detect_procedure_type_qwen
from procedure_schema import (
    get_sample_procedure, get_procedure_by_key, list_procedures,
    merge_qwen_values, compute_missing_required,
    validate_required_values, flatten_fields,
)

app = FastAPI()

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# ── Load Whisper ───────────────────────────────────────────────────────────────
print("Loading Whisper model...")
whisper_model = whisper.load_model("medium")
print("Whisper model loaded!")

# ── Init DB & upload folder ────────────────────────────────────────────────────
init_db()
UPLOAD_FOLDER = "audios"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ── Constants ──────────────────────────────────────────────────────────────────
MAX_TRANSCRIPT_LEN = 1500
SUSPICIOUS_PATTERNS = [
    r"<\s*script", r"</\s*script\s*>",
    r"\bdrop\s+table\b", r"\bunion\s+select\b",
    r"\bdelete\s+from\b", r"\binsert\s+into\b",
    r"\bupdate\s+\w+\s+set\b",
    r";\s*--", r"\bor\s+1\s*=\s*1\b",
]
ALLOWED_ROLES = {"user", "admin"}

STOP_TRAILER = re.compile(r"[,.\s]*\bstop\s+recording\b[\s.!?]*$", re.IGNORECASE)


# ── Helpers ────────────────────────────────────────────────────────────────────
def sanitize_transcript(t: str) -> str:
    cleaned = re.sub(r"[<>{}|`$\\]", "", t)
    cleaned = re.sub(r"[\x00-\x1f\x7f]", " ", cleaned)
    return cleaned.strip()


def strip_stop_word(t: str) -> str:
    return STOP_TRAILER.sub("", t).strip()


def validate_transcript(t: str):
    if not t or not t.strip():
        return {"code": "EMPTY_INPUT", "message": "Transcript is empty."}
    if len(t) > MAX_TRANSCRIPT_LEN:
        return {"code": "INPUT_TOO_LONG",
                "message": f"Transcript exceeds {MAX_TRANSCRIPT_LEN} characters."}
    lower = t.lower()
    for pat in SUSPICIOUS_PATTERNS:
        if re.search(pat, lower):
            return {"code": "SUSPICIOUS_INPUT",
                    "message": "Transcript contains suspicious patterns."}
    return None


# ── POST /upload-audio ─── transcribe only ────────────────────────────────────
@app.post("/upload-audio")
async def upload_audio(
    file: UploadFile = File(...),
    x_user_id:   str = Header(default=None),
    x_user_role: str = Header(default="user"),
):
    start_time = time.time()
    try:
        if not x_user_id:
            return {"success": False, "code": "NO_USER",
                    "error": "Missing X-User-Id header.",
                    "processing_time_ms": int((time.time() - start_time) * 1000)}
        if x_user_role not in ALLOWED_ROLES:
            return {"success": False, "code": "BAD_ROLE",
                    "error": f"Invalid role '{x_user_role}'.",
                    "processing_time_ms": int((time.time() - start_time) * 1000)}

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_path = os.path.join(UPLOAD_FOLDER, f"recording_{timestamp}.webm")
        contents = await file.read()
        with open(file_path, "wb") as f:
            f.write(contents)
        print(f"Audio saved: {file_path}")

        result = whisper_model.transcribe(file_path, task="translate", temperature=0.0)
        raw_transcript     = result["text"].strip()
        cleaned_transcript = strip_stop_word(raw_transcript)
        print(f"Transcript: {cleaned_transcript}")

        return {
            "success":            True,
            "transcript":         cleaned_transcript,
            "raw_transcript":     raw_transcript,
            "audio_file":         file_path,
            "timestamp":          timestamp,
            "processing_time_ms": int((time.time() - start_time) * 1000),
        }
    except Exception as e:
        print("ERROR /upload-audio:", str(e))
        return {"success": False, "error": str(e),
                "processing_time_ms": int((time.time() - start_time) * 1000)}


# ── POST /process-procedure ─── QWEN fills schema ─────────────────────────────
class ProcessProcedureBody(BaseModel):
    approved_text: str
    audio_file:    Optional[str] = None
    timestamp:     Optional[str] = None


@app.post("/process-procedure")
async def process_procedure(
    body: ProcessProcedureBody,
    x_user_id:   str = Header(default=None),
    x_user_role: str = Header(default="user"),
):
    start_time = time.time()
    try:
        # Identity
        if not x_user_id:
            return {"success": False, "api_error":
                    {"code": "NO_USER", "message": "Missing X-User-Id header."},
                    "processing_time_ms": int((time.time() - start_time) * 1000)}
        if x_user_role not in ALLOWED_ROLES:
            return {"success": False, "api_error":
                    {"code": "BAD_ROLE", "message": f"Invalid role '{x_user_role}'."},
                    "processing_time_ms": int((time.time() - start_time) * 1000)}

        # Sanitize + validate
        text = sanitize_transcript(body.approved_text or "")
        v_err = validate_transcript(text)
        if v_err:
            return {"success": False, "approved_text": text, "api_error": v_err,
                    "processing_time_ms": int((time.time() - start_time) * 1000)}

        print(f"\n=== /process-procedure user={x_user_id} role={x_user_role} ===")
        print(f"Text: {text}")

        # Step 1: detect which procedure type the dictation matches
        procedures   = list_procedures()
        detection    = detect_procedure_type_qwen(text, procedures)
        detected_key = detection.get("key")
        print(f"Detected procedure: {detected_key!r} (from {len(procedures)} candidates)")

        # Step 2: load the matching schema (falls back to default if detection failed)
        schema = get_procedure_by_key(detected_key) if detected_key else get_sample_procedure()

        # Step 3: extract fields against THAT schema
        qwen = extract_procedure_fields_qwen(text, schema)
        print(f"QWEN extracted: {qwen['fields']}")

        # Merge QWEN values into schema (skips locked fields)
        merged_schema    = merge_qwen_values(schema, qwen["fields"])
        missing_required = compute_missing_required(merged_schema)
        ai_filled_keys   = [
            f["key"] for f in flatten_fields(merged_schema) if f.get("filled_by_ai")
        ]

        # Log to DB
        timestamp = body.timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
        insert_recording(
            file_path=body.audio_file or "",
            transcript=text, intent="fill_procedure", confidence=None,
            entities=json.dumps(qwen["fields"]), timestamp=timestamp,
            patient_name=str(merged_schema["activeProcedure"]["patientMiniState"]["patientId"]),
            action_type="fill_procedure",
            user_id=x_user_id, user_role=x_user_role,
            result_status="awaiting_approval",
        )

        proc_obj = merged_schema.get("activeProcedure", {})
        return {
            "success":            True,
            "approved_text":      text,
            "detected_procedure": {
                "key":           detected_key,
                "label":         proc_obj.get("offering", {}).get("label", ""),
                "service":       proc_obj.get("service", {}).get("label", ""),
                "auto_detected": detected_key is not None,
                "candidates":    [{"key": p["key"], "label": p["label"]} for p in procedures],
                "detection_raw": detection.get("raw", ""),
            },
            "schema":             merged_schema,
            "ai_filled_keys":     ai_filled_keys,
            "missing_required":   missing_required,
            "can_approve":        len(missing_required) == 0,
            "qwen_raw":           qwen.get("raw", ""),
            "user_id":            x_user_id,
            "user_role":          x_user_role,
            "processing_time_ms": int((time.time() - start_time) * 1000),
        }
    except Exception as e:
        print("ERROR /process-procedure:", str(e))
        return {"success": False, "error": str(e),
                "processing_time_ms": int((time.time() - start_time) * 1000)}


# ── POST /approve-procedure ─── final save (mock) ─────────────────────────────
class ApproveProcedureBody(BaseModel):
    approved_text: Optional[str] = ""
    field_values:  Dict[str, Any]
    audio_file:    Optional[str] = None
    timestamp:     Optional[str] = None
    procedure_key: Optional[str] = None


@app.post("/approve-procedure")
async def approve_procedure(
    body: ApproveProcedureBody,
    x_user_id:   str = Header(default=None),
    x_user_role: str = Header(default="user"),
):
    start_time = time.time()
    try:
        if not x_user_id:
            return {"success": False, "api_error":
                    {"code": "NO_USER", "message": "Missing X-User-Id header."},
                    "processing_time_ms": int((time.time() - start_time) * 1000)}
        if x_user_role not in ALLOWED_ROLES:
            return {"success": False, "api_error":
                    {"code": "BAD_ROLE", "message": f"Invalid role '{x_user_role}'."},
                    "processing_time_ms": int((time.time() - start_time) * 1000)}

        # Validate required fields against the correct schema
        missing = validate_required_values(body.field_values or {}, body.procedure_key)
        if missing:
            return {"success": False,
                    "api_error": {
                        "code":    "MISSING_REQUIRED",
                        "message": "Cannot approve — required fields are still empty.",
                        "missing": missing,
                    },
                    "processing_time_ms": int((time.time() - start_time) * 1000)}

        # Build mock API call + response using the matching schema
        schema   = get_procedure_by_key(body.procedure_key) if body.procedure_key else get_sample_procedure()
        act_id   = schema["activeProcedure"]["effectiveActId"]
        api_call = {
            "method":   "PUT",
            "endpoint": f"PUT /api/procedures/{act_id}",
            "patient":  schema["activeProcedure"]["patientMiniState"]["patientId"],
            "message":  f"Saving procedure execution for actId={act_id}",
        }
        mock_response = {
            "mock":         True,
            "source":       "Simulated Seraph backend (mock execution layer)",
            "status_code":  200,
            "status":       "success",
            "data": {
                "effectiveActId":  act_id,
                "patientId":       schema["activeProcedure"]["patientMiniState"]["patientId"],
                "saved_fields":    body.field_values,
                "saved_at":        datetime.now().isoformat(),
            },
        }

        timestamp = body.timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
        insert_recording(
            file_path=body.audio_file or "",
            transcript=body.approved_text or "", intent="approve_procedure", confidence=None,
            entities=json.dumps(body.field_values), timestamp=timestamp,
            patient_name=str(schema["activeProcedure"]["patientMiniState"]["patientId"]),
            action_type="approve_procedure",
            user_id=x_user_id, user_role=x_user_role,
            result_status="success",
        )

        return {
            "success":            True,
            "api_call":           api_call,
            "mock_response":      mock_response,
            "confirmation": {
                "action":  "approve_procedure",
                "patient": str(schema["activeProcedure"]["patientMiniState"]["patientId"]),
                "status":  "success",
                "message": f"Procedure {act_id} saved successfully.",
            },
            "processing_time_ms": int((time.time() - start_time) * 1000),
        }
    except Exception as e:
        print("ERROR /approve-procedure:", str(e))
        return {"success": False, "error": str(e),
                "processing_time_ms": int((time.time() - start_time) * 1000)}


# ══════════════════════════════════════════════════════════════════════════════
# SDK ENDPOINT — used by external integrators (e.g. Seraph) via MPA SDK.
# Accepts a client-provided procedure schema and returns it filled by Qwen.
# ══════════════════════════════════════════════════════════════════════════════
class SdkProcessBody(BaseModel):
    text:       str
    schema:     Dict[str, Any]
    patient_id: Optional[str] = None
    audio_file: Optional[str] = None
    timestamp:  Optional[str] = None


@app.post("/sdk/process-procedure")
async def sdk_process_procedure(
    body: SdkProcessBody,
    x_user_id:   str = Header(default="sdk_user"),
    x_user_role: str = Header(default="user"),
    authorization: Optional[str] = Header(default=None),
):
    """Client-provided schema entry point. Seraph sends text + schema; we fill it."""
    import copy as _copy
    start_time = time.time()
    try:
        if not authorization:
            return {"success": False, "api_error":
                    {"code": "NO_TOKEN", "message": "Missing Authorization header."},
                    "processing_time_ms": int((time.time() - start_time) * 1000)}

        text  = sanitize_transcript(body.text or "")
        v_err = validate_transcript(text)
        if v_err:
            return {"success": False, "approved_text": text, "api_error": v_err,
                    "processing_time_ms": int((time.time() - start_time) * 1000)}

        if not body.schema or "activeProcedure" not in body.schema:
            return {"success": False, "api_error":
                    {"code": "BAD_SCHEMA",
                     "message": "schema must contain an 'activeProcedure' object."},
                    "processing_time_ms": int((time.time() - start_time) * 1000)}

        print(f"\n=== /sdk/process-procedure patient={body.patient_id!r} ===")
        print(f"Text: {text}")

        qwen = extract_procedure_fields_qwen(text, body.schema)
        print(f"QWEN extracted: {qwen['fields']}")

        merged_schema    = merge_qwen_values(_copy.deepcopy(body.schema), qwen["fields"])
        missing_required = compute_missing_required(merged_schema)
        ai_filled_keys   = [
            f["key"] for f in flatten_fields(merged_schema) if f.get("filled_by_ai")
        ]

        timestamp = body.timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
        insert_recording(
            file_path=body.audio_file or "",
            transcript=text, intent="sdk_fill", confidence=None,
            entities=json.dumps(qwen["fields"]), timestamp=timestamp,
            patient_name=str(body.patient_id or ""),
            action_type="sdk_fill",
            user_id=x_user_id, user_role=x_user_role,
            result_status="awaiting_approval",
        )

        return {
            "success":            True,
            "patient_id":         body.patient_id,
            "approved_text":      text,
            "schema":             merged_schema,
            "ai_filled_keys":     ai_filled_keys,
            "missing_required":   missing_required,
            "can_approve":        len(missing_required) == 0,
            "qwen_raw":           qwen.get("raw", ""),
            "processing_time_ms": int((time.time() - start_time) * 1000),
        }
    except Exception as e:
        print("ERROR /sdk/process-procedure:", str(e))
        return {"success": False, "error": str(e),
                "processing_time_ms": int((time.time() - start_time) * 1000)}


# ── Procedure registry inspection ─────────────────────────────────────────────
@app.get("/procedure-types")
def get_procedure_types():
    procs = list_procedures()
    return {
        "count":      len(procs),
        "procedures": [
            {"key": p["key"], "label": p["label"],
             "service": p["service"], "description": p["description"]}
            for p in procs
        ],
    }


# ── Logs ──────────────────────────────────────────────────────────────────────
@app.get("/recordings")
def get_recordings():
    return {"recordings": get_all_recordings()}


@app.get("/logs/by-user/{user_id}")
def logs_by_user(user_id: str):
    return {"recordings": get_recordings_by_user(user_id), "filter": {"user_id": user_id}}


@app.get("/logs/by-date/{date}")
def logs_by_date(date: str):
    return {"recordings": get_recordings_by_date(date.replace("-", "")),
            "filter": {"date": date}}
