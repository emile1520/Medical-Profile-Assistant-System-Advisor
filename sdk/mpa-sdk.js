(function (global) {
  'use strict';

  // Default to relative "/api" so the SDK works behind nginx without CORS
  // when embedded on the same origin as the backend. Override by passing
  // { apiBase: 'http://your-host:8000' } to MPAClient for local dev.
  var DEFAULT_API_BASE = '/api';

  // Wake phrases — case-insensitive substring match against what Web Speech
  // API hears. "Start recording" is the PRIMARY phrase because the Web Speech
  // API has it in its English vocabulary and transcribes it reliably.
  //
  // "Hey Seraph" is also accepted (Tomorrow Services' preferred phrase), but
  // SR doesn't know the word "seraph" and frequently mishears it as siri,
  // sarah, serie, set off, etc. The phonetic neighbours below catch the most
  // common misrecognitions so users who try "hey seraph" can still trigger.
  //
  // Deliberately EXCLUDED to avoid false positives:
  //   - "hey siri"     → activates Apple devices in the room
  //   - "hey set off"  → too generic, common conversation
  //   - "hey set up"   → too generic
  //   - "hey sherman"  → common name
  // If you find another mishearing in production, add it here.
  var WAKE_VARIANTS = [
    // Primary — reliably recognized by Web Speech API
    'start recording', 'start record', 'begin recording',
    'start the recording', 'start a recording',
    // Secondary — Tomorrow Services' preferred phrase + phonetic neighbours
    // because SR doesn't reliably transcribe "seraph"
    'hey seraph', 'hi seraph',
    'hey sarah', 'hi sarah', 'hey sara',
    'hey serie', 'hey ciri',
    'hey scarab', 'hey seraf', 'hey saraph'
  ];
  var STOP_VARIANTS = [
    'stop recording', 'stop record', 'stop the recording',
    'end recording', 'end record'
  ];

  // ── Client ────────────────────────────────────────────────────────────────
  function MPAClient(options) {
    this._token   = options.token;
    this._userId  = options.userId || ('sdk_' + (options.token || 'anon').slice(0, 6));
    this._apiBase = (options.apiBase || DEFAULT_API_BASE).replace(/\/$/, '');

    this._patientId = null;
    this._schema    = null;

    this._schemas   = null;     // map of { key: { schema, keywords, patientId, label } }
    this._autoMode  = false;    // when true, _processAudio picks a schema from _schemas

    this._listening   = false;
    this._recording   = false;
    this._isDestroyed = false;
    this._state       = 'idle';

    this._mediaRecorder  = null;
    this._audioChunks    = [];
    this._stream         = null;
    this._wakeRecognizer = null;
    this._stopRecognizer = null;

    this._callbacks = {
      data: [], transcript: [], error: [], stateChange: [],
      wakeHeard: [], procedureDetected: []
    };
  }

  // ── Internals ────────────────────────────────────────────────────────────
  MPAClient.prototype._setState = function (s) {
    this._state = s;
    this._emit('stateChange', s);
  };
  MPAClient.prototype._emit = function (channel, payload) {
    var list = this._callbacks[channel] || [];
    for (var i = 0; i < list.length; i++) {
      try { list[i](payload); }
      catch (err) { console.error('[MPA callback error]', err); }
    }
  };
  MPAClient.prototype._headers = function (isJson) {
    var h = { 'X-User-Id': this._userId };
    if (this._token) h['Authorization'] = 'Bearer ' + this._token;
    if (isJson) h['Content-Type'] = 'application/json';
    return h;
  };

  // ── Public: register the current patient case ────────────────────────────
  // Accepts either:
  //   • a schema object (any shape — the backend walks it recursively for
  //     fields with key/label/fieldType). Synchronous, returns `this`.
  //   • a spec `{ schemaUrl: "..." }` — fetches from the URL, returns a Promise
  MPAClient.prototype.startPatientCase = function (patientIdentifier, schemaOrSpec) {
    var self = this;
    self._patientId = patientIdentifier;
    // Calling startPatientCase explicitly always overrides auto-detect mode.
    // Otherwise a prior startAutoCase() call would still win and we'd try to
    // score the transcript against the registered candidate schemas instead
    // of using the schema the caller just passed.
    self._autoMode = false;

    if (!schemaOrSpec || typeof schemaOrSpec !== 'object') {
      throw new Error('[MPA] startPatientCase requires a schema object or { schemaUrl } spec.');
    }

    // Treat anything with a schemaUrl as a fetch spec; everything else is a direct schema.
    if (schemaOrSpec.schemaUrl && typeof schemaOrSpec.schemaUrl === 'string') {
      return self.fetchSchema(schemaOrSpec.schemaUrl).then(function (schema) {
        self._schema = schema;
        console.log('[MPA] Patient case started (fetched schema):', patientIdentifier);
        return schema;
      });
    }

    // Direct schema, any shape. The backend will reject it if it contains
    // no fillable fields (each field needs key + label + fieldType).
    self._schema = schemaOrSpec;
    console.log('[MPA] Patient case started (direct schema):', patientIdentifier);
    return self;
  };

  // ── Public: fetch a single empty schema from Seraph (or any URL) ─────────
  // Returns a Promise that resolves to the fetched schema (validated).
  MPAClient.prototype.fetchSchema = function (url) {
    var self = this;
    return fetch(url, { method: 'GET', headers: self._headers(false) })
      .then(function (r) {
        if (!r.ok) throw new Error('Schema fetch failed: HTTP ' + r.status);
        return r.json();
      })
      .then(function (schema) {
        if (!schema || typeof schema !== 'object') {
          throw new Error('Fetched schema is not a valid object.');
        }
        // Shape validation is the backend's job — we accept any non-empty
        // object here. Backend will return BAD_SCHEMA if no fillable fields.
        return schema;
      })
      .catch(function (err) {
        self._emit('error', { code: 'SCHEMA_FETCH_FAILED', message: err.message || String(err) });
        throw err;
      });
  };

  // ── Public: fetch a *map* of candidate schemas (for auto-detect) ─────────
  // The URL must return JSON shaped like:
  //   { "key1": { schema, keywords, patientId, label }, "key2": {...}, ... }
  // After fetching, the schemas are registered with the SDK automatically.
  MPAClient.prototype.fetchSchemas = function (url) {
    var self = this;
    return fetch(url, { method: 'GET', headers: self._headers(false) })
      .then(function (r) {
        if (!r.ok) throw new Error('Schemas fetch failed: HTTP ' + r.status);
        return r.json();
      })
      .then(function (schemasMap) {
        if (!schemasMap || typeof schemasMap !== 'object' || Array.isArray(schemasMap)) {
          throw new Error('Fetched data is not a valid schemas map.');
        }
        self._schemas = schemasMap;
        console.log('[MPA] Fetched ' + Object.keys(schemasMap).length + ' schemas from', url);
        return schemasMap;
      })
      .catch(function (err) {
        self._emit('error', { code: 'SCHEMAS_FETCH_FAILED', message: err.message || String(err) });
        throw err;
      });
  };

  MPAClient.prototype.endPatientCase = function () {
    this._patientId = null;
    this._schema    = null;
    this._autoMode  = false;
    return this;
  };

  MPAClient.prototype.getState = function () { return this._state; };

  // ── Public: auto-detect mode ────────────────────────────────────────────
  // Register a set of candidate schemas. Each entry: { schema, keywords, patientId, label }.
  // After recording, the SDK picks the best-matching one by keyword score against
  // the transcript and uses it for /sdk/process-procedure.
  MPAClient.prototype.registerSchemas = function (schemasMap) {
    if (!schemasMap || typeof schemasMap !== 'object') {
      throw new Error('[MPA] registerSchemas requires an object map of { key: { schema, keywords, patientId } }.');
    }
    this._schemas = schemasMap;
    return this;
  };

  MPAClient.prototype.startAutoCase = function () {
    if (!this._schemas || !Object.keys(this._schemas).length) {
      throw new Error('[MPA] registerSchemas() must be called before startAutoCase().');
    }
    this._autoMode  = true;
    this._patientId = null;
    this._schema    = null;
    console.log('[MPA] Auto-detect case started. Candidates:', Object.keys(this._schemas));
    return this;
  };

  // Score each registered schema's keywords against the transcript. Returns
  // { key, score, runnerUpScore } or { key: null } if no keyword hit.
  MPAClient.prototype._pickSchemaByKeywords = function (text) {
    if (!this._schemas) return { key: null, score: 0, runnerUpScore: 0 };
    var low = (text || '').toLowerCase();
    var best = { key: null, score: 0 };
    var runnerUp = 0;
    for (var key in this._schemas) {
      if (!Object.prototype.hasOwnProperty.call(this._schemas, key)) continue;
      var kws = this._schemas[key].keywords || [];
      var s = 0;
      for (var i = 0; i < kws.length; i++) {
        if (kws[i] && low.indexOf(String(kws[i]).toLowerCase()) !== -1) s++;
      }
      if (s > best.score) { runnerUp = best.score; best = { key: key, score: s }; }
      else if (s > runnerUp) { runnerUp = s; }
    }
    return { key: best.score > 0 ? best.key : null, score: best.score, runnerUpScore: runnerUp };
  };

  // ── Public: register callbacks ───────────────────────────────────────────
  MPAClient.prototype.onDataReceived       = function (cb) { this._callbacks.data.push(cb);              return this; };
  MPAClient.prototype.onTranscript         = function (cb) { this._callbacks.transcript.push(cb);        return this; };
  MPAClient.prototype.onError              = function (cb) { this._callbacks.error.push(cb);             return this; };
  MPAClient.prototype.onStateChange        = function (cb) { this._callbacks.stateChange.push(cb);       return this; };
  MPAClient.prototype.onWakeHeard          = function (cb) { this._callbacks.wakeHeard.push(cb);         return this; };
  MPAClient.prototype.onProcedureDetected  = function (cb) { this._callbacks.procedureDetected.push(cb); return this; };

  // ── Public: voice activation (wake-word listener) ────────────────────────
  MPAClient.prototype.startListening = function () {
    if (this._listening) return this;
    var SR = global.SpeechRecognition || global.webkitSpeechRecognition;
    if (!SR) {
      this._emit('error', { code: 'NO_SPEECH_API',
        message: 'SpeechRecognition not supported in this browser. Use Chrome or Edge.' });
      return this;
    }
    this._listening   = true;
    this._isDestroyed = false;
    this._setState('listening');

    var self = this;
    var listen = function () {
      if (self._isDestroyed || self._recording) return;
      var r = new SR();
      self._wakeRecognizer = r;
      r.continuous     = false;
      r.interimResults = false;
      r.lang           = 'en-US';
      r.maxAlternatives = 3;

      r.onresult = function (event) {
        var heard = '';
        for (var i = 0; i < event.results.length; i++) {
          for (var j = 0; j < event.results[i].length; j++) {
            heard += event.results[i][j].transcript.toLowerCase() + ' ';
          }
        }
        var cleaned = heard.trim();
        if (cleaned) self._emit('wakeHeard', cleaned);
        for (var k = 0; k < WAKE_VARIANTS.length; k++) {
          if (cleaned.indexOf(WAKE_VARIANTS[k]) !== -1 && !self._recording) {
            self.startRecording();
            return;
          }
        }
      };

      r.onerror = function (event) {
        if (event && (event.error === 'not-allowed' || event.error === 'service-not-allowed')) {
          self._isDestroyed = true;
          self._listening   = false;
          self._setState('idle');
          self._emit('error', {
            code:    'WAKE_PERMISSION_DENIED',
            message: 'Speech recognition permission denied. Allow microphone access in the browser.'
          });
        } else if (event && event.error && event.error !== 'no-speech' && event.error !== 'aborted') {
          console.warn('[MPA wake-word]', event.error);
        }
      };

      r.onend = function () {
        if (!self._isDestroyed && !self._recording) setTimeout(listen, 300);
      };

      try { r.start(); } catch (_) { setTimeout(listen, 500); }
    };
    setTimeout(listen, 200);
    return this;
  };

  MPAClient.prototype.stopListening = function () {
    this._isDestroyed = true;
    this._listening   = false;
    try { this._wakeRecognizer && this._wakeRecognizer.stop(); } catch (_) {}
    try { this._stopRecognizer && this._stopRecognizer.stop(); } catch (_) {}
    if (this._state === 'listening') this._setState('idle');
    return this;
  };

  // ── Public: manual recording control ─────────────────────────────────────
  MPAClient.prototype.startRecording = function () {
    if (this._recording) return this;
    if (!this._schema && !this._autoMode) {
      this._emit('error', { code: 'NO_CASE',
        message: 'startPatientCase() or startAutoCase() must be called before recording.' });
      return this;
    }
    var self = this;
    self._recording   = true;
    self._audioChunks = [];
    self._setState('recording');

    navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
      self._stream = stream;
      var mr = new MediaRecorder(stream, { mimeType: 'audio/webm' });
      self._mediaRecorder = mr;
      mr.ondataavailable = function (e) {
        if (e.data && e.data.size > 0) self._audioChunks.push(e.data);
      };
      mr.onstop = function () {
        stream.getTracks().forEach(function (t) { t.stop(); });
        self._processAudio();
      };
      mr.start();
      self._startStopWordListener();
    }).catch(function () {
      self._recording = false;
      self._setState('idle');
      self._emit('error', { code: 'MIC_DENIED', message: 'Microphone access denied.' });
    });
    return this;
  };

  MPAClient.prototype._startStopWordListener = function () {
    var SR = global.SpeechRecognition || global.webkitSpeechRecognition;
    if (!SR || this._stopRecognizer) return;
    var self = this;
    var sw = new SR();
    this._stopRecognizer = sw;
    sw.continuous = true; sw.interimResults = true; sw.lang = 'en-US';
    sw.onresult = function (event) {
      var heard = '';
      for (var i = event.resultIndex; i < event.results.length; i++) {
        heard += event.results[i][0].transcript.toLowerCase() + ' ';
      }
      var text = heard.trim();
      for (var k = 0; k < STOP_VARIANTS.length; k++) {
        if (text.indexOf(STOP_VARIANTS[k]) !== -1 && self._recording) {
          self.stopRecording();
          return;
        }
      }
    };
    sw.onend = function () {
      if (self._recording && self._stopRecognizer === sw) {
        try { sw.start(); } catch (_) {}
      }
    };
    try { sw.start(); } catch (_) {}
  };

  MPAClient.prototype.stopRecording = function () {
    if (!this._recording) return this;
    this._recording = false;
    if (this._mediaRecorder && this._mediaRecorder.state !== 'inactive') {
      this._mediaRecorder.stop();
    }
    try { this._stopRecognizer && this._stopRecognizer.stop(); } catch (_) {}
    this._stopRecognizer = null;
    return this;
  };

  // ── Pipeline: transcribe → extract → fire callback ──────────────────────
  MPAClient.prototype._processAudio = function () {
    var self = this;
    self._setState('transcribing');

    var blob = new Blob(self._audioChunks, { type: 'audio/webm' });
    var fd = new FormData();
    fd.append('file', blob, 'recording.webm');

    fetch(self._apiBase + '/upload-audio', {
      method:  'POST',
      headers: self._headers(false),
      body:    fd
    })
    .then(function (r) { return r.json(); })
    .then(function (data) {
      if (!data.success) throw new Error(data.error || 'Transcription failed.');
      var transcript = data.transcript || '';
      self._emit('transcript', {
        text:       transcript,
        audio_file: data.audio_file,
        timestamp:  data.timestamp
      });

      if (!transcript.trim()) {
        self._setState('idle');
        self._emit('error', {
          code:    'EMPTY_TRANSCRIPT',
          message: 'No speech detected in the recording. Try again and speak clearly between start and stop.'
        });
        return null;
      }

      // Auto-detect: pick the best-matching registered schema by keyword score.
      if (self._autoMode) {
        var pick = self._pickSchemaByKeywords(transcript);
        if (!pick.key) {
          self._setState('idle');
          self._emit('error', {
            code:    'NO_PROCEDURE_MATCH',
            message: 'Could not match the dictation to any registered procedure.'
          });
          return null;
        }
        var entry = self._schemas[pick.key];
        // Deep clone so the template isn't mutated across runs
        self._schema    = JSON.parse(JSON.stringify(entry.schema));
        self._patientId = entry.patientId || self._patientId;
        self._emit('procedureDetected', {
          key:           pick.key,
          label:         entry.label || pick.key,
          score:         pick.score,
          runnerUpScore: pick.runnerUpScore
        });
      }

      self._setState('processing');
      return fetch(self._apiBase + '/sdk/process-procedure', {
        method:  'POST',
        headers: self._headers(true),
        body:    JSON.stringify({
          text:       transcript,
          schema:     self._schema,
          patient_id: self._patientId,
          audio_file: data.audio_file,
          timestamp:  data.timestamp
        })
      });
    })
    .then(function (r) { return r ? r.json() : null; })
    .then(function (data) {
      if (!data) return;
      if (!data.success) {
        throw new Error((data.api_error && data.api_error.message) || data.error || 'Extraction failed.');
      }
      self._setState('done');
      self._emit('data', {
        patient_id:         self._patientId,
        transcript:         data.approved_text,
        schema:             data.schema,
        ai_filled_keys:     data.ai_filled_keys || [],
        missing_required:   data.missing_required || [],
        can_approve:        data.can_approve,
        processing_time_ms: data.processing_time_ms
      });
    })
    .catch(function (err) {
      self._setState('idle');
      self._emit('error', { code: 'PIPELINE_FAILED', message: err.message || String(err) });
    });
  };

  // ── Factory ──────────────────────────────────────────────────────────────
  var MPA = {
    init: function (options) {
      if (!options || !options.token) {
        throw new Error('[MPA] init() requires a token. Usage: MPA.init({ token: "..." })');
      }
      console.log('[MPA SDK v1.0.0] Initialised for', options.userId || '(token-only session)');
      return new MPAClient(options);
    },
    version: '1.0.0'
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = MPA;
  }
  global.MPA = MPA;
})(typeof window !== 'undefined' ? window : this);
