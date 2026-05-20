# MPA — Deployment Playbook

Target: **https://mpa.tomorrow.services/** → served from `forge@3.136.112.231:/home/forge/mpa`.

## What this deploys

```
   Seraph (their site)                            mpa.tomorrow.services
   ──────────────────────                         ─────────────────────────────
   <script src="https://mpa.tomorrow.services/sdk/mpa-sdk.js">
                                                       │
                                                       ▼
                                                  nginx (TLS, /api proxy, static)
                                                       │
                            ┌──────────────────────────┼──────────────────────────┐
                            ▼                          ▼                          ▼
                       /  → sdk/example.html       /sdk/mpa-sdk.js          /api/* ──► FastAPI ──► Whisper
                       /mock-seraph/...            (CORS: *)                 (uvicorn)    BART + NER
                                                                                          Ollama (Qwen3:1.7b)
```

- The **React frontend is not deployed**. It was only your local test harness.
- The **SDK file** (`sdk/mpa-sdk.js`) is what Seraph embeds in their own site.
- The **demo page** (`sdk/example.html`) is served at the root URL so you and
  the Seraph dev team can verify the pipeline live, without writing any code.
- The **backend** runs as a systemd service; nginx reverse-proxies `/api/*` to it.

This playbook deploys exactly that on a Linux server. Run each section in order.

> When a command starts with `sudo`, you'll get a password prompt if `forge`
> has sudo. If it doesn't, ask Tomorrow Services for sudo access first.

---

## 0. SSH in

On your laptop:

```bash
ssh forge@3.136.112.231
```

Everything below runs **on the server** unless I say otherwise.

---

## 1. Figure out the server state

```bash
# Is this a Laravel Forge-managed server?
ls -la /home/forge/.forge 2>/dev/null && echo "→ Forge-managed" || echo "→ Plain server"

# OS + RAM + disk
cat /etc/os-release | head -2
free -h
df -h /

# What's already installed?
which python3 nginx ffmpeg certbot ollama 2>/dev/null
python3 --version 2>/dev/null
nginx -v        2>&1
```

**If Forge-managed:** add `mpa.tomorrow.services` as a site in the Forge
dashboard (pick "Static HTML"), then later you'll paste my `nginx-mpa.conf`
into Forge's nginx editor for that site. Or skip the dashboard and just
drop the config file directly (Section 6) — Forge won't know about it but
nginx will.

**If RAM < 8 GB:** stop and tell Charbel. Whisper-medium + BART + NER + Qwen
needs ~8-10 GB resident. If it's tight, we'll switch Whisper to "small".

---

## 2. Install system packages

```bash
sudo apt update
sudo apt install -y \
    python3 python3-venv python3-dev python3-pip \
    build-essential \
    ffmpeg \
    git \
    nginx \
    certbot python3-certbot-nginx
```

No Node.js needed — we're not building a React app anymore. (Node only matters
if you keep developing the local frontend test harness.)

---

## 3. Install Ollama + pull the LLM

```bash
curl -fsSL https://ollama.com/install.sh | sh
sudo systemctl enable --now ollama
ollama --version

# Pull the model the backend uses.
ollama pull qwen3:1.7b

# Smoke-test.
ollama run qwen3:1.7b "Say hello in one word."
```

If `qwen3:1.7b` isn't available anymore, check https://ollama.com/library/qwen3
for the current tag and update the `QWEN_MODEL` constant near the top of
`backend/llm.py`.

---

## 4. Get the code onto the server

Pick one — **git is strongly preferred** for future updates.

### Option A — Git (recommended)

```bash
cd /home/forge
git clone <your-github-url> mpa
cd mpa
```

If the repo isn't on GitHub yet, push it first from your laptop:

```bash
git remote add origin git@github.com:<your-org>/medical-profile-assistant.git
git push -u origin main
```

### Option B — rsync from your laptop

```bash
# Run on your laptop, from the parent of the project folder:
rsync -avz --delete \
    --exclude '.git' \
    --exclude 'node_modules' \
    --exclude 'venv' \
    --exclude 'backend/venv' \
    --exclude 'backend/__pycache__' \
    --exclude 'backend/audios' \
    --exclude 'backend/medical_assistant.db' \
    --exclude 'frontend' \
    Medical-Profile-Assistant-System-Advisor-main/ \
    forge@3.136.112.231:/home/forge/mpa/
```

(Note the `--exclude 'frontend'` — we don't need it on the server.)

After either option, you should have on the server:
```
/home/forge/mpa/
    backend/
    sdk/                ← demo + the SDK file Seraph loads
        example.html
        mpa-sdk.js
        mock-seraph/schemas-catalog.json
    deploy/             ← configs + this playbook
```

---

## 5. Build & install the backend

```bash
cd /home/forge/mpa/backend

python3 -m venv venv
source venv/bin/activate

# IMPORTANT: CPU-only torch first — otherwise pip pulls the multi-GB CUDA build.
pip install --upgrade pip
pip install --index-url https://download.pytorch.org/whl/cpu torch==2.11.0

pip install -r requirements.txt

# Pre-download the Whisper model (~1.5 GB) so the first request isn't 5 minutes.
python -c "import whisper; whisper.load_model('medium')"

# Pre-download HuggingFace models (~2 GB total).
python -c "
from transformers import pipeline
pipeline('zero-shot-classification', model='facebook/bart-large-mnli')
pipeline('token-classification', model='d4data/biomedical-ner-all', aggregation_strategy='simple')
"

# Smoke-test — should print 'Whisper model loaded!' then start uvicorn.
# Ctrl+C once you see 'Uvicorn running on http://127.0.0.1:8000'.
uvicorn main:app --host 127.0.0.1 --port 8000
```

In a second SSH session, verify it answers locally:

```bash
curl http://127.0.0.1:8000/procedure-types
```

Then Ctrl+C the uvicorn process — systemd will take over.

---

## 6. Install the systemd service

```bash
sudo cp /home/forge/mpa/deploy/mpa-backend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mpa-backend

# Watch first boot — takes 30-60s while it loads Whisper + BART + NER.
sudo journalctl -u mpa-backend -f
# Ctrl+C once you see "Application startup complete."

# Verify it's listening locally:
curl http://127.0.0.1:8000/procedure-types
```

If it crash-loops, the log tail tells you why. Common causes: missing ffmpeg,
wrong venv path in the service file, out-of-memory (drop to whisper-small).

---

## 7. Install the nginx site

```bash
sudo cp /home/forge/mpa/deploy/nginx-mpa.conf \
        /etc/nginx/sites-available/mpa.tomorrow.services

# Check what else is enabled — make sure nothing else is grabbing this host.
ls -la /etc/nginx/sites-enabled/

# Enable our site
sudo ln -s /etc/nginx/sites-available/mpa.tomorrow.services \
           /etc/nginx/sites-enabled/

# Test config & reload
sudo nginx -t
sudo systemctl reload nginx

# Verify
curl -I http://mpa.tomorrow.services/                          # should serve example.html
curl    http://mpa.tomorrow.services/healthz                   # should print "ok"
curl    http://mpa.tomorrow.services/sdk/mpa-sdk.js | head -5  # should be JS
curl    http://mpa.tomorrow.services/api/procedure-types       # should be JSON
```

> If you went the Forge dashboard route, paste `nginx-mpa.conf` into the
> site's nginx editor and click Save — Forge handles the reload.

---

## 8. Get an SSL certificate

DNS must already point `mpa.tomorrow.services` → `3.136.112.231`. Confirm:

```bash
dig +short mpa.tomorrow.services
```

Then:

```bash
sudo certbot --nginx -d mpa.tomorrow.services
# Pick "redirect HTTP → HTTPS" when prompted.
```

Certbot auto-renews via systemd timer:
```bash
sudo systemctl list-timers | grep certbot
```

After this, the four URLs in Section 7 should all work over **https**.

---

## 9. Smoke test the full pipeline

Open `https://mpa.tomorrow.services/` in a browser. You should see the SDK
demo page. Click **Start auto-detect case**, then **Start recording**, say
something like *"adding a composite filling on tooth 14"*, then **Stop recording**.

In a second terminal:

```bash
ssh forge@3.136.112.231
sudo journalctl -u mpa-backend -f
```

You should see the request land, Whisper transcribe, and Qwen extract fields.
The page's "Detected" pill should update and the form below should fill in.

---

## 10. Hand it off to Seraph

Tell the Seraph dev team:

> The SDK is at **`https://mpa.tomorrow.services/sdk/mpa-sdk.js`**.
> Drop a script tag on your page:
>
> ```html
> <script src="https://mpa.tomorrow.services/sdk/mpa-sdk.js"></script>
> <script>
>   const mpa = MPA.init({
>     token:  'their-auth-token',
>     userId: 'DR-MEZHER'
>   });
>   // apiBase defaults to "https://mpa.tomorrow.services/api" — no override needed.
>
>   // Register the empty schemas you want auto-filled:
>   mpa.fetchSchemas('https://your-seraph-host/path/to/schemas-catalog.json');
>
>   mpa.onDataReceived((data) => {
>     // data.schema is the same schema you sent, with fields filled in.
>     // data.ai_filled_keys lists which fields the model populated.
>     console.log(data);
>   });
>
>   // Start a case, then record:
>   mpa.startAutoCase();
>   mpa.startRecording();
>   // ... later:
>   mpa.stopRecording();
> </script>
> ```
>
> A working live example is at **`https://mpa.tomorrow.services/`** — view source
> to see the full integration pattern.
>
> CORS is currently `*` (any origin can call the API). Send me your production
> origin and I'll tighten it.

---

## 11. Future updates

```bash
ssh forge@3.136.112.231
cd /home/forge/mpa
git pull

# If backend changed:
cd backend
source venv/bin/activate
pip install -r requirements.txt   # only if requirements.txt changed
sudo systemctl restart mpa-backend

# If sdk/ changed: no restart needed — nginx serves files directly from disk.
```

---

## Troubleshooting cheatsheet

| Symptom | Where to look |
|---|---|
| 502 Bad Gateway on /api/* | `sudo journalctl -u mpa-backend -n 200` — backend crashed |
| Whisper/HF download stalls | `df -h`, and check `~/.cache/` is writable by `forge` |
| `ModuleNotFoundError` on startup | `pip install -r requirements.txt` after pull |
| Ollama "model not found" | `ollama list`, then `ollama pull qwen3:1.7b` |
| CORS error in Seraph's browser console | The SDK file *is* served with `Access-Control-Allow-Origin: *`. If you see CORS on `/api/*` calls, FastAPI's CORSMiddleware in `backend/main.py` should be checked (currently `allow_origins=["*"]`) |
| Big request times out | Bump `proxy_read_timeout` in `nginx-mpa.conf` |
| Out of memory | Switch `whisper.load_model("medium")` to `"small"` in `backend/main.py` |
| Demo page works on localhost but not on the server | Check `/var/log/nginx/error.log`; usually a missing file path in the alias directive |

---

## Files in `deploy/`

| File | Where it ends up | Purpose |
|---|---|---|
| `nginx-mpa.conf` | `/etc/nginx/sites-available/mpa.tomorrow.services` | nginx server block |
| `mpa-backend.service` | `/etc/systemd/system/mpa-backend.service` | systemd unit for uvicorn |
| `DEPLOY.md` | this file | the playbook |

Plus `backend/requirements.txt` at the project root of `backend/`.
