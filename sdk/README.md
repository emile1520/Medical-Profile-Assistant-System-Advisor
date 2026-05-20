# MPA SDK — Medical Profile Assistant

Voice-driven procedure filling for the Seraph platform.

The MPA SDK is a single JavaScript file that lets Seraph add AI-powered voice
charting to any patient page. The dentist talks; the SDK transcribes their
speech, has Qwen extract the structured fields, and hands the filled procedure
record back to Seraph's frontend via a callback.

**Seraph owns the form. MPA owns the brain.**

---

## Quick start

```html
<!-- 1. Drop in the SDK -->
<script src="mpa-sdk.js"></script>

<script>
  // 2. Initialise once per page with the practitioner's token
  const mpa = MPA.init({
    token:   'TOKEN_FROM_SERAPH_AUTH',
    userId:  'DR-MEZHER',         // optional, used for audit logs
    apiBase: 'https://mpa.your.domain'  // optional — defaults to localhost
  });

  // 3. Register the callback that updates Seraph's UI
  mpa.onDataReceived((data) => {
    // data.schema is the procedure with values filled by the AI
    // data.ai_filled_keys is the list of field keys the AI filled
    // data.missing_required is the list of required fields still empty
    fillSeraphForm(data.schema);
  });

  // 4. When the dentist opens a patient case, tell MPA what to fill
  document.getElementById('start-procedure').addEventListener('click', () => {
    mpa.startPatientCase('PT-44213', procedureSchema);
    mpa.startListening();   // voice activation — or use manual start/stop
  });
</script>
```

That's the entire integration. See `example.html` for a working demo.

---

## How it works (one paragraph)

The dentist clicks "start procedure" on Seraph's patient page. Seraph calls
`mpa.startPatientCase(patientId, schema)` — the schema is whatever procedure
structure Seraph wants filled (any shape, any fields). The SDK records the
dentist's voice via the browser mic, posts the audio to the MPA backend
(Whisper transcribes it locally), then posts the transcript + the schema to
`/sdk/process-procedure` (Qwen extracts the field values). The backend returns
the same schema with values filled in. The SDK fires the `onDataReceived`
callback Seraph registered, and Seraph updates its own form.

---

## API reference

### `MPA.init(options)` → `MPAClient`

Creates a client. Call once per page.

| Option    | Type   | Required | Description |
|-----------|--------|----------|-------------|
| `token`   | string | yes      | Seraph-issued auth token (sent as `Authorization: Bearer …`) |
| `userId`  | string | no       | Practitioner identifier used for backend audit logs |
| `apiBase` | string | no       | MPA backend URL (e.g. `https://mpa.example.com`). Defaults to `http://127.0.0.1:8000`. |

### `client.startPatientCase(patientIdentifier, procedureStructure)`

Registers the current case. Must be called **before** recording.

- `patientIdentifier` — any string Seraph uses to identify the patient (logged only)
- `procedureStructure` — the schema MPA will fill. Must contain an `activeProcedure` object with `variants[]`, `findings[]`, and `results.fields[]`. See "Schema shape" below.

### Recording — two modes

**Voice activation (hands-free):**
```js
client.startListening();   // listens for "start recording"
client.stopListening();    // turns off voice activation
```
When the dentist says *"start recording"*, the SDK starts capturing. When they say *"stop recording"*, it stops and processes automatically.

**Manual (wire to buttons):**
```js
client.startRecording();   // call from a "record" button
client.stopRecording();    // call from a "stop" button
```

### Callbacks

```js
client.onDataReceived((data) => { /* the filled procedure */ });
client.onTranscript((t)   => { /* raw transcript from Whisper */ });
client.onError((err)      => { /* { code, message } */ });
client.onStateChange((s)  => { /* idle | listening | recording | transcribing | processing | done */ });
```

The `data` payload passed to `onDataReceived`:

```js
{
  patient_id:         'PT-44213',
  transcript:         'Class two composite filling on tooth 14, mesial and occlusal...',
  schema:             { activeProcedure: { ... } },   // your schema, with values filled
  ai_filled_keys:     ['restoration.surfaces', 'caries.depth', ...],
  missing_required:   [{ key: 'polish_completed', label: 'Polish completed', section: 'results' }],
  can_approve:        false,    // true iff missing_required is empty
  processing_time_ms: 4218
}
```

### Other methods

```js
client.endPatientCase();   // clear current case
client.getState();         // current state string
```

---

## Schema shape

The `procedureStructure` Seraph passes follows this shape. Every field in
`variants`, `findings`, and `results.fields` will be considered for AI filling.

```js
{
  activeProcedure: {
    effectiveActId: 77001,
    procedureKey:   'seraph_composite_demo',  // your identifier
    status:         'in_progress',
    service:    { key: 'restorative', label: 'Restorative Dentistry' },
    offering:   { id: 612, label: 'Composite Filling',
                  description: 'Tooth-coloured restoration.' },
    patientMiniState: { patientId: 'PT-44213', age: 31, sex: 'female',
                        medical: { allergies: { value: [] } } },
    caseContext: { caseId: 9001,
                   scope: { type: 'tooth', value: '14', label: 'Tooth 14' } },

    variants: [ /* fields */ ],
    findings: [ /* fields */ ],
    results:  { schemaKey: '...', fields: [ /* fields */ ] }
  }
}
```

Each field has this shape:

```js
{
  key:         'restoration.shade',  // unique identifier you'll receive back
  label:       'Shade',
  fieldType:   'select',             // see below
  required:    false,                // true ⇒ must be filled before approval
  locked:      false,                // true ⇒ AI must NOT overwrite this value
  value:       null,                 // initial value (use null/[] for empty)
  options:     [{ value, label }, ...],  // for select / multi_select
  unit:        'mm',                 // for number fields
  description: 'Brief hint for the AI about what this field means'
}
```

### Supported `fieldType` values

| Type           | Value shape                          | UI hint           |
|----------------|--------------------------------------|-------------------|
| `select`       | string (one of `options[].value`)    | dropdown          |
| `multi_select` | array of strings from `options`      | checkbox group    |
| `boolean`      | `true` / `false`                     | Yes / No          |
| `number`       | number (with optional `unit`)        | numeric input     |
| `tags`         | array of strings                     | free-form tags    |
| `textarea`     | string                               | multi-line text   |
| `tooth_picker` | string (FDI tooth number)            | text input        |

The AI returns values that match these shapes — Seraph's renderer can decide
how each field looks on screen.

---

## Backend requirements

The SDK calls two endpoints on the MPA backend:

| Endpoint                       | Method | Purpose                                |
|--------------------------------|--------|----------------------------------------|
| `/upload-audio`                | POST   | Audio → transcript via Whisper         |
| `/sdk/process-procedure`       | POST   | Transcript + schema → filled schema    |

Both expect:
- `Authorization: Bearer <token>` header
- `X-User-Id` header for audit logging

CORS must allow Seraph's origin. The reference backend currently uses
`allow_origins=["*"]` for development; tighten to Seraph's exact origin in
production.

---

## Production checklist

The current SDK is a **demo build**. Before shipping with real patients:

1. **Token validation** — backend currently accepts any non-empty Bearer token. Wire it to Seraph's JWT/session validator.
2. **CORS** — restrict `allow_origins` to Seraph's exact production origin.
3. **HTTPS only** — `getUserMedia` requires a secure context on non-localhost domains. Serve the SDK and backend over HTTPS.
4. **PII** — audio files are currently written to disk under `backend/audios/`. Consider an in-memory pipeline + retention policy.
5. **Browser support** — wake-word detection uses the Web Speech API (Chrome / Edge / Safari). Manual `startRecording()` / `stopRecording()` works everywhere modern.
6. **Versioning** — pin the SDK version Seraph hosts (`mpa-sdk.v1.0.0.js`) so backend / SDK changes can roll out independently.

---

## Files in this folder

```
sdk/
├── mpa-sdk.js     ← the SDK itself (drop into Seraph's HTML)
├── example.html   ← working integration example (open in a browser)
└── README.md      ← this file
```

To try the example: open `example.html` in Chrome with the MPA backend running
at `http://127.0.0.1:8000`. Click *Start procedure*, then *Start recording*,
dictate something like *"Class two composite filling on tooth 14, mesial and
occlusal surfaces, shade A2, light cured"*, click *Stop*. The Seraph-side form
fields will fill in (teal background = AI-filled), and the raw response is
available in the collapsible panel.
