import React, { useState, useRef, useEffect, useCallback, useMemo } from "react";

const API_BASE = "http://127.0.0.1:8000";

const WAKE_VARIANTS = [
  "start recording", "start record", "begin recording",
  "start the recording", "start a recording",
];
const STOP_VARIANTS = [
  "stop recording", "stop record", "stop the recording",
  "end recording", "end record",
];

function playChime(freq = 880) {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain); gain.connect(ctx.destination);
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0.4, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.2);
    osc.start(); osc.stop(ctx.currentTime + 0.2);
  } catch (_) {}
}

// ── Utility: walk schema → flat list of editable fields ──────────────────────
function flattenFields(schema) {
  if (!schema) return [];
  const proc = schema.activeProcedure || {};
  const out = [];
  (proc.variants || []).forEach((f) => out.push({ ...f, section: "variants" }));
  (proc.findings || []).forEach((f) => out.push({ ...f, section: "findings" }));
  ((proc.results || {}).fields || []).forEach((f) => out.push({ ...f, section: "results" }));
  return out;
}

function isEmpty(v) {
  return v === null || v === undefined || v === ""
       || (Array.isArray(v) && v.length === 0);
}

// ── Single field row ─────────────────────────────────────────────────────────
function FieldRow({ field, value, onChange }) {
  const locked       = !!field.locked;
  const filledByAi   = !!field.filled_by_ai;
  const required     = !!field.required;
  const empty        = isEmpty(value);
  const missingReq   = required && empty && !locked;

  let statusBadge = null;
  if (locked)            statusBadge = <span className="field-badge field-badge--locked">🔒 Locked</span>;
  else if (filledByAi)   statusBadge = <span className="field-badge field-badge--ai">✅ AI</span>;
  else if (missingReq)   statusBadge = <span className="field-badge field-badge--required">⚠ Required</span>;
  else if (!empty)       statusBadge = <span className="field-badge field-badge--ok">●</span>;

  const renderInput = () => {
    const disabled = locked;
    switch (field.fieldType) {
      case "select":
        return (
          <select
            disabled={disabled}
            value={value ?? ""}
            onChange={(e) => onChange(e.target.value || null)}
          >
            <option value="">— select —</option>
            {(field.options || []).map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        );

      case "multi_select":
        return (
          <div className="checkbox-group">
            {(field.options || []).map((o) => {
              const arr = Array.isArray(value) ? value : [];
              const checked = arr.includes(o.value);
              return (
                <label key={o.value} className="checkbox-label">
                  <input
                    type="checkbox"
                    disabled={disabled}
                    checked={checked}
                    onChange={(e) => {
                      const next = e.target.checked
                        ? [...arr, o.value]
                        : arr.filter((x) => x !== o.value);
                      onChange(next);
                    }}
                  />
                  {o.label}
                </label>
              );
            })}
          </div>
        );

      case "boolean":
        return (
          <div className="bool-group">
            <button
              type="button" disabled={disabled}
              className={`bool-btn ${value === true ? "active" : ""}`}
              onClick={() => onChange(true)}
            >Yes</button>
            <button
              type="button" disabled={disabled}
              className={`bool-btn ${value === false ? "active" : ""}`}
              onClick={() => onChange(false)}
            >No</button>
          </div>
        );

      case "number":
        return (
          <div className="number-row">
            <input
              type="number" disabled={disabled}
              value={value ?? ""}
              onChange={(e) => {
                const v = e.target.value;
                onChange(v === "" ? null : parseFloat(v));
              }}
            />
            {field.unit && <span className="unit">{field.unit}</span>}
          </div>
        );

      case "tags": {
        const tags = Array.isArray(value) ? value : [];
        return (
          <input
            type="text" disabled={disabled}
            value={tags.join(", ")}
            onChange={(e) =>
              onChange(
                e.target.value.split(",").map((s) => s.trim()).filter(Boolean)
              )
            }
            placeholder="comma-separated"
          />
        );
      }

      case "textarea":
        return (
          <textarea
            disabled={disabled}
            value={value ?? ""}
            onChange={(e) => onChange(e.target.value)}
            rows={3}
          />
        );

      case "tooth_picker":
      default:
        return (
          <input
            type="text" disabled={disabled}
            value={value ?? ""}
            onChange={(e) => onChange(e.target.value)}
          />
        );
    }
  };

  return (
    <div className={`field-row ${missingReq ? "field-row--missing" : ""}`}>
      <div className="field-head">
        <label className="field-label">
          {field.label}
          {required && <span className="req-star"> *</span>}
        </label>
        {statusBadge}
      </div>
      {renderInput()}
      {field.description && <div className="field-desc">{field.description}</div>}
    </div>
  );
}

// ── Section block ────────────────────────────────────────────────────────────
function FieldSection({ title, fields, values, onChange }) {
  if (!fields.length) return null;
  return (
    <div className="form-section">
      <div className="form-section__title">{title}</div>
      {fields.map((f) => (
        <FieldRow
          key={f.key}
          field={f}
          value={values[f.key]}
          onChange={(v) => onChange(f.key, v)}
        />
      ))}
    </div>
  );
}

// ── Main component ───────────────────────────────────────────────────────────
export default function Recorder() {
  // recording / wake
  const [recording,  setRecording]  = useState(false);
  const [wakeActive, setWakeActive] = useState(false);
  const [wakeReady,  setWakeReady]  = useState(false);
  const [wakeError,  setWakeError]  = useState("");
  const [lastHeard,  setLastHeard]  = useState("");

  // pipeline phase
  const [phase, setPhase]             = useState("idle"); // idle | transcribing | review | extracting | form | approving | done
  const [transcript, setTranscript]   = useState("");
  const [editedText, setEditedText]   = useState("");
  const [audioMeta,  setAudioMeta]    = useState(null);

  // schema + form state
  const [schema, setSchema]           = useState(null);
  const [fieldValues, setFieldValues] = useState({});
  const [missingRequired, setMissingRequired] = useState([]);
  const [aiFilledKeys, setAiFilledKeys]       = useState([]);

  // mock save result
  const [apiCall, setApiCall]             = useState(null);
  const [mockResponse, setMockResponse]   = useState(null);
  const [confirmation, setConfirmation]   = useState(null);
  const [apiError, setApiError]           = useState(null);
  const [processingTime, setProcessingTime] = useState(null);
  const [error, setError]                 = useState("");

  // identity
  const [userId,   setUserId]   = useState("dr_smith");
  const [userRole, setUserRole] = useState("user");

  // history
  const [history, setHistory]         = useState([]);
  const [showHistory, setShowHistory] = useState(false);

  // refs
  const mediaRecorderRef = useRef(null);
  const audioChunksRef   = useRef([]);
  const isRecording      = useRef(false);
  const isDestroyed      = useRef(false);
  const wakeRecognizerRef = useRef(null);
  const stopRecognizerRef = useRef(null);

  // ── reset
  const resetPipeline = useCallback(() => {
    setTranscript(""); setEditedText("");
    setSchema(null); setFieldValues({});
    setMissingRequired([]); setAiFilledKeys([]);
    setApiCall(null); setMockResponse(null);
    setConfirmation(null); setApiError(null);
    setProcessingTime(null); setError("");
  }, []);

  // ── history fetch
  const fetchHistory = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/recordings`);
      const data = await res.json();
      setHistory(data.recordings || []);
    } catch (_) {}
  }, []);

  useEffect(() => { fetchHistory(); }, [fetchHistory]);

  // ── Phase 1: upload audio → transcript
  const uploadAudio = useCallback(async () => {
    setPhase("transcribing");
    const blob = new Blob(audioChunksRef.current, { type: "audio/webm" });
    const formData = new FormData();
    formData.append("file", blob, "recording.webm");
    try {
      const res = await fetch(`${API_BASE}/upload-audio`, {
        method: "POST",
        headers: { "X-User-Id": userId, "X-User-Role": userRole },
        body: formData,
      });
      const data = await res.json();
      if (data.success) {
        setTranscript(data.transcript || "");
        setEditedText(data.transcript || "");
        setAudioMeta({ audio_file: data.audio_file, timestamp: data.timestamp });
        setProcessingTime(data.processing_time_ms ?? null);
        setPhase("review");
      } else {
        setError(data.error || "Transcription failed.");
        setPhase("idle");
      }
    } catch (_) {
      setError("Could not reach the backend on port 8000.");
      setPhase("idle");
    }
  }, [userId, userRole]);

  // ── Phase 2: send approved text to QWEN → load form
  const sendToQwen = useCallback(async () => {
    if (!editedText.trim()) { setError("Approved text is empty."); return; }
    setPhase("extracting");
    setError("");
    try {
      const res = await fetch(`${API_BASE}/process-procedure`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-User-Id":    userId,
          "X-User-Role":  userRole,
        },
        body: JSON.stringify({
          approved_text: editedText,
          audio_file:    audioMeta?.audio_file ?? null,
          timestamp:     audioMeta?.timestamp ?? null,
        }),
      });
      const data = await res.json();

      if (!data.success) {
        setApiError(data.api_error || { code: "ERROR", message: data.error || "Unknown" });
        setProcessingTime(data.processing_time_ms ?? null);
        setPhase("review");
        return;
      }

      // Initialize fieldValues from the merged schema
      const flat = flattenFields(data.schema);
      const initial = {};
      flat.forEach((f) => { initial[f.key] = f.value; });

      setSchema(data.schema);
      setFieldValues(initial);
      setMissingRequired(data.missing_required || []);
      setAiFilledKeys(data.ai_filled_keys || []);
      setProcessingTime(data.processing_time_ms ?? null);
      setPhase("form");
    } catch (_) {
      setError("Failed to reach /process-procedure.");
      setPhase("review");
    }
  }, [editedText, audioMeta, userId, userRole]);

  // ── Phase 3: approve form → mock save
  const approveProcedure = useCallback(async () => {
    setPhase("approving");
    setError(""); setApiError(null);
    try {
      const res = await fetch(`${API_BASE}/approve-procedure`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-User-Id":    userId,
          "X-User-Role":  userRole,
        },
        body: JSON.stringify({
          approved_text: editedText,
          field_values:  fieldValues,
          audio_file:    audioMeta?.audio_file ?? null,
          timestamp:     audioMeta?.timestamp ?? null,
        }),
      });
      const data = await res.json();

      if (data.success) {
        setApiCall(data.api_call || null);
        setMockResponse(data.mock_response || null);
        setConfirmation(data.confirmation || null);
        setProcessingTime(data.processing_time_ms ?? null);
        setPhase("done");
        fetchHistory();
      } else {
        setApiError(data.api_error || { code: "ERROR", message: data.error || "Unknown" });
        setProcessingTime(data.processing_time_ms ?? null);
        setPhase("form");
      }
    } catch (_) {
      setError("Failed to reach /approve-procedure.");
      setPhase("form");
    }
  }, [editedText, fieldValues, audioMeta, userId, userRole, fetchHistory]);

  // ── Update single field value
  const updateField = useCallback((key, value) => {
    setFieldValues((prev) => ({ ...prev, [key]: value }));
  }, []);

  // ── Recompute missing required when fieldValues change
  useEffect(() => {
    if (!schema) return;
    const missing = flattenFields(schema)
      .filter((f) => f.required && !f.locked && isEmpty(fieldValues[f.key]))
      .map((f) => ({ key: f.key, label: f.label, section: f.section }));
    setMissingRequired(missing);
  }, [fieldValues, schema]);

  // ── Stop recording
  const stopRecording = useCallback(() => {
    isRecording.current = false;
    setRecording(false); setWakeActive(false);
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
      mediaRecorderRef.current.stop();
    }
    try { stopRecognizerRef.current?.stop(); } catch (_) {}
    stopRecognizerRef.current = null;
    playChime(440);
  }, []);

  // ── Stop-word listener (parallel during recording)
  const startStopWordListener = useCallback(() => {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR || stopRecognizerRef.current) return;
    const sw = new SR();
    stopRecognizerRef.current = sw;
    sw.continuous = true; sw.interimResults = true; sw.lang = "en-US";
    sw.onresult = (event) => {
      let heard = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        heard += event.results[i][0].transcript.toLowerCase() + " ";
      }
      const text = heard.trim();
      if (STOP_VARIANTS.some((v) => text.includes(v)) && isRecording.current) {
        stopRecording();
      }
    };
    sw.onend = () => {
      if (isRecording.current && stopRecognizerRef.current === sw) {
        try { sw.start(); } catch (_) {}
      }
    };
    try { sw.start(); } catch (_) {}
  }, [stopRecording]);

  // ── Start recording
  const startRecording = useCallback(async () => {
    if (isRecording.current) return;
    isRecording.current = true;
    resetPipeline();
    setRecording(true); setPhase("idle");
    try {
      audioChunksRef.current = [];
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mr = new MediaRecorder(stream, { mimeType: "audio/webm" });
      mediaRecorderRef.current = mr;
      mr.ondataavailable = (e) => { if (e.data.size > 0) audioChunksRef.current.push(e.data); };
      mr.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        uploadAudio();
      };
      mr.start();
      startStopWordListener();
    } catch (_) {
      setError("Microphone access denied.");
      isRecording.current = false; setRecording(false);
    }
  }, [uploadAudio, startStopWordListener, resetPipeline]);

  // ── Wake-word listener (idle)
  useEffect(() => {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) { setWakeError("Use Chrome or Edge for wake-word detection."); return; }
    isDestroyed.current = false;
    const listen = () => {
      if (isDestroyed.current || isRecording.current) return;
      const r = new SR();
      wakeRecognizerRef.current = r;
      r.continuous = false; r.interimResults = false; r.lang = "en-US"; r.maxAlternatives = 3;
      r.onstart = () => setWakeReady(true);
      r.onresult = (event) => {
        let heard = "";
        for (let i = 0; i < event.results.length; i++) {
          for (let j = 0; j < event.results[i].length; j++) {
            heard += event.results[i][j].transcript.toLowerCase() + " ";
          }
        }
        const cleaned = heard.trim();
        setLastHeard(cleaned);
        if (WAKE_VARIANTS.some((v) => cleaned.includes(v)) && !isRecording.current) {
          playChime(); setWakeActive(true); startRecording();
        }
      };
      r.onend = () => {
        if (!isDestroyed.current && !isRecording.current) setTimeout(listen, 300);
      };
      try { r.start(); } catch (_) { setTimeout(listen, 500); }
    };
    const initTimer = setTimeout(listen, 500);
    return () => {
      isDestroyed.current = true;
      clearTimeout(initTimer);
      try { wakeRecognizerRef.current?.stop(); } catch (_) {}
      try { stopRecognizerRef.current?.stop();  } catch (_) {}
    };
  }, [startRecording]);

  // ── Derived
  const fieldsBySection = useMemo(() => {
    const flat = flattenFields(schema);
    return {
      variants: flat.filter((f) => f.section === "variants"),
      findings: flat.filter((f) => f.section === "findings"),
      results:  flat.filter((f) => f.section === "results"),
    };
  }, [schema]);

  const canApprove = schema && missingRequired.length === 0 && phase === "form";

  // ── Render
  return (
    <div className="container">
      <div className="card">

        <div className="identity-bar">
          <label>User ID:
            <input type="text" value={userId} onChange={(e) => setUserId(e.target.value)} className="identity-input" />
          </label>
          <label>Role:
            <select value={userRole} onChange={(e) => setUserRole(e.target.value)} className="identity-select">
              <option value="user">user</option>
              <option value="admin">admin</option>
            </select>
          </label>
        </div>

        <div className="wake-status">
          {wakeError ? (
            <span className="wake-pill wake-pill--error">⚠ {wakeError}</span>
          ) : wakeActive ? (
            <span className="wake-pill wake-pill--active">🔵 Recording… say "stop recording" to end</span>
          ) : wakeReady ? (
            <span className="wake-pill wake-pill--idle">🟢 Say "Start Recording" to begin</span>
          ) : (
            <span className="wake-pill wake-pill--idle">⏳ Initialising…</span>
          )}
        </div>

        {lastHeard && (
          <div style={{ fontSize: 12, color: "#94a3b8", marginBottom: 8 }}>
            🎤 Last heard: <em>{lastHeard}</em>
          </div>
        )}

        {schema && (
          <div className="procedure-header">
            <div className="procedure-header__row">
              <span><strong>Service:</strong> {schema.activeProcedure.service.label}</span>
              <span><strong>Offering:</strong> {schema.activeProcedure.offering.label}</span>
            </div>
            <div className="procedure-header__row">
              <span><strong>Patient:</strong> ID {schema.activeProcedure.patientMiniState.patientId}, {schema.activeProcedure.patientMiniState.age}y, {schema.activeProcedure.patientMiniState.sex}</span>
              <span><strong>Tooth:</strong> {schema.activeProcedure.caseContext.scope.label}</span>
            </div>
          </div>
        )}

        <div className="btn-row">
          {!recording ? (
            <button className="button start" onClick={startRecording} disabled={phase === "transcribing" || phase === "extracting" || phase === "approving"}>
              🎤 Start Recording
            </button>
          ) : (
            <button className="button stop" onClick={stopRecording}>⏹ Stop Recording</button>
          )}
        </div>

        {recording && (
          <div className="recording-indicator">
            <span className="pulse-dot" />Recording… say "stop recording" to finish
          </div>
        )}

        {phase === "transcribing" && <div className="processing-label">⏳ Whisper transcribing…</div>}
        {phase === "extracting"   && <div className="processing-label">⏳ QWEN extracting fields from your text…</div>}
        {phase === "approving"    && <div className="processing-label">⏳ Saving procedure…</div>}

        {error && <div className="error-box">{error}</div>}

        {/* PHASE: REVIEW */}
        {(phase === "review" || phase === "extracting") && transcript && (
          <div className="result-box review-box">
            <div className="result-box__label">📝 Transcript — Review & Edit</div>
            <textarea
              className="transcript-edit"
              value={editedText}
              onChange={(e) => setEditedText(e.target.value)}
              rows={4}
              disabled={phase !== "review"}
            />
            {phase === "review" && (
              <div className="review-actions">
                <button className="button approve" onClick={sendToQwen}>✅ Approve & Extract Fields</button>
                <button className="button" style={{ background: "#94a3b8", color: "white" }}
                        onClick={() => setEditedText(transcript)}>↻ Reset</button>
              </div>
            )}
          </div>
        )}

        {/* PHASE: FORM */}
        {(phase === "form" || phase === "approving" || phase === "done") && schema && (
          <div className="result-box form-wrap">
            <div className="result-box__label">📋 Procedure Form — review & complete</div>
            <div className="form-summary">
              <span><strong>{aiFilledKeys.length}</strong> filled by AI</span>
              <span><strong>{missingRequired.length}</strong> required missing</span>
            </div>

            <FieldSection title="Variants"  fields={fieldsBySection.variants}  values={fieldValues} onChange={updateField} />
            <FieldSection title="Findings"  fields={fieldsBySection.findings}  values={fieldValues} onChange={updateField} />
            <FieldSection title="Results"   fields={fieldsBySection.results}   values={fieldValues} onChange={updateField} />

            {phase === "form" && (
              <div className="review-actions" style={{ marginTop: 16 }}>
                <button
                  className="button approve"
                  disabled={!canApprove}
                  onClick={approveProcedure}
                >
                  {canApprove ? "✅ Approve & Save Procedure" : `⚠ ${missingRequired.length} required field(s) missing`}
                </button>
              </div>
            )}
          </div>
        )}

        {apiError && (
          <div className="api-call-banner api-call-banner--error">
            <div className="api-call-banner__title">⚠ {apiError.code}</div>
            <div className="api-call-banner__message">{apiError.message}</div>
            {apiError.missing && (
              <ul style={{ margin: "8px 0 0 18px", fontSize: 13 }}>
                {apiError.missing.map((m) => <li key={m.key}>{m.label} <code>({m.section})</code></li>)}
              </ul>
            )}
          </div>
        )}

        {apiCall && (
          <div className={`api-call-banner api-call-banner--${apiCall.method.toLowerCase()}`}>
            <div className="api-call-banner__title">
              🔗 External API Call — <span className="api-call-banner__method">{apiCall.method}</span>
            </div>
            <div className="api-call-banner__message">{apiCall.message}</div>
            <code className="api-call-banner__endpoint">{apiCall.endpoint}</code>
          </div>
        )}

        {mockResponse && (
          <div className="result-box mock-response">
            <div className="result-box__label">🧪 Simulated Backend Response</div>
            <div className="mock-response__meta">
              {mockResponse.source} · status {mockResponse.status_code} · {mockResponse.status}
            </div>
            <pre className="mock-response__body">{JSON.stringify(mockResponse.data, null, 2)}</pre>
          </div>
        )}

        {confirmation && (
          <div className="result-box confirmation-box">
            <div className="result-box__label">✅ Confirmation</div>
            <div className="confirmation-grid">
              <span><strong>Action:</strong> {confirmation.action}</span>
              <span><strong>Patient:</strong> {confirmation.patient}</span>
              <span><strong>Status:</strong> {confirmation.status}</span>
            </div>
            <div className="confirmation-message">{confirmation.message}</div>
          </div>
        )}

        {processingTime !== null && (
          <div className="processing-time">⏱ Processed in {processingTime} ms</div>
        )}

        <div style={{ marginTop: 28 }}>
          <button className="button" style={{ backgroundColor: "#6c7a89", color: "white" }}
                  onClick={() => { setShowHistory((v) => !v); if (!showHistory) fetchHistory(); }}>
            {showHistory ? "Hide History" : "Show Recording History"}
          </button>
        </div>

        {showHistory && (
          <div className="result-box" style={{ marginTop: 16, textAlign: "left" }}>
            <div className="result-box__label">🗂 Recording History</div>
            {history.length === 0 ? (
              <p style={{ color: "#888", fontSize: 14 }}>No recordings yet.</p>
            ) : (
              history.map((rec) => (
                <div key={rec[0]} className="history-item">
                  <div className="history-item__date">{rec[6]} · {rec[8] || "?"} · {rec[10] || "?"}</div>
                  <div className="history-item__text">{rec[2]}</div>
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
}
