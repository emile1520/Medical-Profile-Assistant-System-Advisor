"""
Standalone CLI: send a schema + transcript to Qwen, get back filled values.

Usage:
    cd backend
    .\venv\Scripts\Activate.ps1     # PowerShell
    python test_qwen_fill.py schema.json "transcript text here"

The script does NOT touch the FastAPI app — it talks to Ollama directly,
the same way `extract_procedure_fields_qwen` does in llm.py. Use it to
debug the LLM step in isolation when you suspect Whisper or the FastAPI
plumbing is fine but the extraction itself is off.

Requirements:
    - Ollama running locally on http://127.0.0.1:11434 with qwen3:1.7b pulled
    - `pip install ollama` (already in backend/requirements.txt)
"""
import json
import re
import sys
from pathlib import Path

import ollama

QWEN_MODEL = "qwen3:1.7b"


def flatten_cylinder_fields(schema: dict) -> list[str]:
    """
    Walks a schema shaped like:
        activeProcedure.fields.<key> = { type, label, options[].id, options[].name, ... }
    Returns one line per field that's reasonable for Qwen to fill.
    """
    lines = []
    proc = schema.get("activeProcedure", {})
    fields = proc.get("fields", {})

    for key, f in fields.items():
        ftype = f.get("type", "text")
        label = f.get("label", key)
        opts = f.get("options")

        line = f"- {key} [{ftype}]"
        if opts:
            # Options for cylinder schema use {id, name}. Show first 20 then "..."
            vals = []
            for o in opts[:20]:
                if isinstance(o, dict):
                    vals.append(str(o.get("id", o.get("name", ""))))
                else:
                    vals.append(str(o))
            tail = ", ..." if len(opts) > 20 else ""
            line += f" allowed_values=[{', '.join(vals)}{tail}]"
        if label and label != key:
            line += f" — {label}"
        lines.append(line)
    return lines


def build_prompt(schema: dict, text: str) -> str:
    field_lines = "\n".join(flatten_cylinder_fields(schema))
    return f"""/no_think
You are a domain scribe AI. Extract field values from the user's dictation and map them to the structured schema below.

AVAILABLE FIELDS (key [type] allowed_values — label):
{field_lines}

OUTPUT RULES:
- Output ONLY a single valid JSON object: {{"field_key": value, ...}}
- For "select" fields, the value must be EXACTLY one of allowed_values (use the id, not the name).
- For "boolean", use true or false.
- For "number", use a numeric value (no quotes).
- For "text", use a string.
- ONLY include fields that are clearly mentioned or implied in the dictation. Skip everything else.
- Do NOT invent data. If unsure, omit the field.
- No markdown, no explanation, no thinking — just the JSON.

DICTATION:
\"\"\"{text}\"\"\"

JSON:"""


def ask_qwen(prompt: str) -> str:
    response = ollama.chat(
        model=QWEN_MODEL,
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": 0.0},
    )
    return response["message"]["content"]


def parse_json_block(raw: str) -> dict:
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", raw, flags=re.IGNORECASE)
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}


def main():
    if len(sys.argv) < 3:
        print("Usage: python test_qwen_fill.py <schema.json> <transcript text>")
        sys.exit(1)

    schema_path = Path(sys.argv[1])
    transcript = " ".join(sys.argv[2:])

    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    prompt = build_prompt(schema, transcript)
    print("=" * 70)
    print("PROMPT SENT TO QWEN:")
    print("=" * 70)
    print(prompt)
    print()

    raw = ask_qwen(prompt)
    print("=" * 70)
    print("QWEN RAW RESPONSE:")
    print("=" * 70)
    print(raw)
    print()

    fields = parse_json_block(raw)
    print("=" * 70)
    print(f"EXTRACTED FIELDS ({len(fields)}):")
    print("=" * 70)
    print(json.dumps(fields, indent=2))
    print()

    ai_filled_keys = list(fields.keys())
    print(f"ai_filled_keys = {ai_filled_keys}")


if __name__ == "__main__":
    main()
