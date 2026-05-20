// In production the React build is served from the same origin as the API
// (nginx proxies /api/* to the FastAPI backend), so a relative "/api" base
// works without CORS. For local dev, set REACT_APP_API_BASE=http://127.0.0.1:8000
// in frontend/.env.local (or fall back to localhost below).
const API_BASE =
  (typeof process !== "undefined" && process.env && process.env.REACT_APP_API_BASE)
    ? process.env.REACT_APP_API_BASE
    : (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1"
        ? "http://127.0.0.1:8000"
        : "/api");

const MedicalAssistant = {
  _token: null,
  _userId: null,

  init({ token, userId }) {
    this._token = token;
    this._userId = userId;
    console.log(`[MedicalAssistant SDK] Initialized — user: ${userId}`);
    return this;
  },

  getHeaders(isJson = false) {
    const h = { "X-User-Id": this._userId };
    if (isJson) h["Content-Type"] = "application/json";
    return h;
  },

  async transcribeAudio(audioBlob) {
    const formData = new FormData();
    formData.append("file", audioBlob, "recording.webm");
    const res = await fetch(`${API_BASE}/upload-audio`, {
      method: "POST",
      headers: this.getHeaders(),
      body: formData,
    });
    return res.json();
  },

  async processProcedure({ approvedText, audioFile, timestamp }) {
    const res = await fetch(`${API_BASE}/process-procedure`, {
      method: "POST",
      headers: this.getHeaders(true),
      body: JSON.stringify({
        approved_text: approvedText,
        audio_file: audioFile ?? null,
        timestamp: timestamp ?? null,
      }),
    });
    return res.json();
  },

  async approveProcedure({ approvedText, fieldValues, audioFile, timestamp, procedureKey }) {
    const res = await fetch(`${API_BASE}/approve-procedure`, {
      method: "POST",
      headers: this.getHeaders(true),
      body: JSON.stringify({
        approved_text: approvedText,
        field_values: fieldValues,
        audio_file: audioFile ?? null,
        timestamp: timestamp ?? null,
        procedure_key: procedureKey ?? null,
      }),
    });
    return res.json();
  },

  async getProcedureTypes() {
    const res = await fetch(`${API_BASE}/procedure-types`, {
      headers: this.getHeaders(),
    });
    return res.json();
  },

  async fetchProcedureContext() {
    const res = await fetch(`${API_BASE}/procedure-context`, {
      headers: this.getHeaders(),
    });
    return res.json();
  },

  async getLogs() {
    const res = await fetch(`${API_BASE}/recordings`, {
      headers: this.getHeaders(),
    });
    return res.json();
  },
};

export default MedicalAssistant;