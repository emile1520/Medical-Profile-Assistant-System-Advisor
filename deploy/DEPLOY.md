# MPA — Deployment Playbook

**Production target:** https://mpa.tomorrow.services/ → `forge@3.136.112.231:/home/forge/mpa`
**Server type:** Ubuntu 24.04 LTS, **Laravel Forge–managed**

## What gets deployed

```
   Seraph (their site, any origin)               mpa.tomorrow.services
   ──────────────────────                        ─────────────────────────────
   <script src="https://mpa.tomorrow.services/sdk/mpa-sdk.js">
                                                       │
                                                       ▼
                                                  nginx (Forge-provisioned TLS)
                                                       │
                            ┌──────────────────────────┼──────────────────────────┐
                            ▼                          ▼                          ▼
                       /  → sdk/example.html       /sdk/mpa-sdk.js          /api/* ──► FastAPI (uvicorn)
                       /mock-seraph/...            (CORS: *)                 ▼          Whisper-medium (STT)
                                                                       127.0.0.1:8000   BART zero-shot + bio-NER
                                                                                        Ollama → Qwen3:1.7B
```

- The React frontend is **not deployed**. It was only your local test harness.
- The **SDK file** (`sdk/mpa-sdk.js`) is what Seraph embeds in their own site.
- The **demo page** (`sdk/example.html`) is served at the root URL so anyone
  can verify the pipeline works without writing code.
- The **backend** runs as a systemd service; nginx reverse-proxies `/api/*`.

---

## ⚠ Two phases (because the initial server was undersized)

The first deploy attempted on a 900 MB RAM box. Whisper-medium + BART +
biomedical NER + Qwen3-1.7B need ~8–10 GB resident. So we did:

- **Phase 1** — SDK + demo + nginx + SSL. Doesn't load any ML models, fits
  in 900 MB easily. This is what's live now.
- **Phase 2** — backend (uvicorn, Whisper, transformers, Ollama). Requires
  the server to be resized to ≥ 8 GB RAM (16 GB recommended).

If you're deploying from scratch on a properly sized server, you can do
both phases back-to-back without the wait.

---

## 0. SSH in

From your laptop:

```bash
ssh forge@3.136.112.231
```

Forge sets up the `forge` user automatically. SSH-key auth is preset by
Forge during server provisioning; add new keys via the Forge dashboard
(*Server → SSH Keys*) rather than editing `~/.ssh/authorized_keys` directly.

Everything below runs **on the server** unless I say otherwise.

---

## 1. Survey the server (3 min)

```bash
ls -la /home/forge/.forge 2>/dev/null && echo "→ Forge-managed" || echo "→ Plain server"
cat /etc/os-release | head -2
free -h
df -h /
which python3 nginx ffmpeg certbot ollama 2>/dev/null
python3 --version 2>/dev/null
nginx -v 2>&1
```

**Pass conditions before continuing:**

| Check | Required |
|---|---|
| `free -h` total Mem | **≥ 8 GB for Phase 2** (Phase 1 OK at any size) |
| Disk free | **≥ 20 GB** (model caches alone are ~5 GB) |
| OS | Ubuntu 22.04 or 24.04 |
| Python | 3.10+ |

If RAM is under 8 GB, do Phase 1 anyway (it's useful — gets the URL live),
then ask Tomorrow Services to resize before starting Phase 2.

---

## 2. Install system packages (Phase 1)

```bash
sudo apt update
sudo apt install -y git certbot python3-certbot-nginx
```

> On Forge-managed servers, **certbot is not needed** for SSL — Forge issues
> certs via its dashboard. We're installing it just to have it available.
>
> nginx is already installed by Forge.
> Skip ffmpeg / python3-venv / etc. until Phase 2 — those are backend deps.

---

## 3. Get the code onto the server

```bash
cd /home/forge
git clone https://github.com/emile1520/Medical-Profile-Assistant-System-Advisor.git mpa
cd mpa
ls
# Expected: backend/  deploy/  sdk/  frontend/  gantt.py  .gitignore
```

For future updates: `cd /home/forge/mpa && git pull`.

---

## 4. Make the SDK directory readable by nginx

nginx runs as the `www-data` user. It needs the execute (`x`) bit on every
parent directory between `/` and the files it serves, and the read (`r`)
bit on the files themselves.

```bash
chmod o+x /home/forge /home/forge/mpa /home/forge/mpa/sdk /home/forge/mpa/sdk/mock-seraph
ls -la /home/forge/mpa/sdk/example.html /home/forge/mpa/sdk/mpa-sdk.js
# Both files should show "-rw-r--r--" — world-readable.
```

Skip this and every request to `/` returns 403 Forbidden.

---

## 5. Install the nginx site (Forge-managed approach)

In the Forge dashboard, `mpa.tomorrow.services` should already exist as a
"Static HTML" site (Forge will have provisioned an SSL cert automatically).
The wrapper config lives at `/etc/nginx/sites-available/mpa.tomorrow.services`
and `include`s a per-site file we own:
**`/etc/nginx/forge-conf/<site-id>/site.conf`**.

Find the site ID Forge assigned:

```bash
SITE_ID=$(grep -oP 'forge-conf/\K[0-9]+' \
    /etc/nginx/sites-available/mpa.tomorrow.services | head -1)
echo "Site ID: $SITE_ID"
```

Back up the default and drop in our config:

```bash
sudo cp /etc/nginx/forge-conf/$SITE_ID/site.conf \
        /etc/nginx/forge-conf/$SITE_ID/site.conf.bak

sudo cp /home/forge/mpa/deploy/forge-site.conf \
        /etc/nginx/forge-conf/$SITE_ID/site.conf

sudo nginx -t && sudo systemctl reload nginx
```

> If you're on a **non-Forge** server, use `deploy/nginx-mpa.conf` instead —
> drop it in `/etc/nginx/sites-available/`, `ln -s` it into `sites-enabled/`,
> and run `sudo certbot --nginx -d mpa.tomorrow.services` for SSL.

---

## 6. Phase 1 smoke test

```bash
curl -I https://mpa.tomorrow.services/
curl    https://mpa.tomorrow.services/healthz
curl -s https://mpa.tomorrow.services/sdk/mpa-sdk.js | head -3
curl -s https://mpa.tomorrow.services/mock-seraph/schemas-catalog.json | head -3
curl -i https://mpa.tomorrow.services/api/procedure-types 2>&1 | head -3
```

| Endpoint | Expected |
|---|---|
| `/` | `HTTP/2 200`, `Content-Type: text/html` |
| `/healthz` | `ok` |
| `/sdk/mpa-sdk.js` | starts with `(function (global) {` |
| `/mock-seraph/...` | starts with `{` |
| `/api/procedure-types` | **502 Bad Gateway** (correct — backend isn't running yet) |

Then open **https://mpa.tomorrow.services/** in a browser — you should see
the SDK demo page. Recording won't work end-to-end until Phase 2; that's fine.

**Phase 1 done.** If RAM is < 8 GB, stop here and request a resize.

---

## 7. Phase 2 — Install Ollama + pull the LLM

Requires **≥ 8 GB RAM** to actually run, but the install/pull itself only
needs disk:

```bash
curl -fsSL https://ollama.com/install.sh | sh
sudo systemctl enable --now ollama
ollama --version
ollama pull qwen3:1.7b           # ~1.5 GB download
ollama run qwen3:1.7b "Say hi."  # smoke test
```

If `qwen3:1.7b` gets renamed by Ollama, check https://ollama.com/library/qwen3
and update the `QWEN_MODEL` constant near the top of `backend/llm.py`.

---

## 8. Phase 2 — Install backend deps (~20 min, mostly downloads)

```bash
sudo apt install -y python3-venv python3-dev python3-pip build-essential ffmpeg

cd /home/forge/mpa/backend
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip

# CPU-only torch FIRST — otherwise pip pulls the multi-GB CUDA build.
pip install --index-url https://download.pytorch.org/whl/cpu torch==2.11.0

pip install -r requirements.txt

# Pre-download the models so the first request isn't a 5-minute hang.
# These ARE blocking on RAM — Whisper-medium needs ~5 GB to load.
python -c "import whisper; whisper.load_model('medium')"
python -c "
from transformers import pipeline
pipeline('zero-shot-classification', model='facebook/bart-large-mnli')
pipeline('token-classification', model='d4data/biomedical-ner-all', aggregation_strategy='simple')
"

# Smoke-test the FastAPI app
uvicorn main:app --host 127.0.0.1 --port 8000
# In another tab: curl http://127.0.0.1:8000/procedure-types
# Then Ctrl+C — systemd will run it for real.
```

---

## 9. Phase 2 — Start the backend service

```bash
sudo cp /home/forge/mpa/deploy/mpa-backend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mpa-backend

sudo journalctl -u mpa-backend -f
# Wait for "Application startup complete." — first boot takes 30-60s.

curl http://127.0.0.1:8000/procedure-types
curl https://mpa.tomorrow.services/api/procedure-types   # no more 502!
```

---

## 10. End-to-end test (Phase 2 done)

Open https://mpa.tomorrow.services/ → *Start auto-detect case* → *Start recording*
→ say *"adding a composite filling on tooth 14"* → *Stop recording*.

While doing this, watch the backend log in another SSH tab:

```bash
sudo journalctl -u mpa-backend -f
```

You should see Whisper transcribe, then Qwen extract fields, and the form
on the page should fill in.

---

## 11. Hand off to the Seraph team

Tell them:

> The SDK is at **`https://mpa.tomorrow.services/sdk/mpa-sdk.js`**.
>
> ```html
> <script src="https://mpa.tomorrow.services/sdk/mpa-sdk.js"></script>
> <script>
>   const mpa = MPA.init({
>     token:  'their-auth-token',
>     userId: 'DR-MEZHER'
>   });
>   // apiBase defaults to https://mpa.tomorrow.services/api — no override needed
>   // when loading the SDK from this origin. From a different origin, the SDK
>   // also defaults to "/api" relative to itself.
>
>   mpa.fetchSchemas('https://seraph-host/your/schemas-catalog.json');
>
>   mpa.onDataReceived((data) => {
>     // data.schema is the schema you sent, with AI-filled fields.
>     // data.ai_filled_keys lists which fields the model populated.
>   });
>
>   mpa.startAutoCase();
>   mpa.startRecording();
>   // ... later: mpa.stopRecording();
> </script>
> ```
>
> Live working example at **https://mpa.tomorrow.services/** — view source.
>
> CORS is `*`. Send me your production origin once you have one and I'll lock
> it down to that single origin in `backend/main.py`.

---

## Future updates

```bash
ssh forge@3.136.112.231
cd /home/forge/mpa
git pull

# Backend changed?
cd backend
source venv/bin/activate
pip install -r requirements.txt   # only if requirements.txt changed
sudo systemctl restart mpa-backend

# SDK or demo changed? No restart needed — nginx serves files directly.

# nginx config changed (deploy/forge-site.conf)?
SITE_ID=$(grep -oP 'forge-conf/\K[0-9]+' \
    /etc/nginx/sites-available/mpa.tomorrow.services | head -1)
sudo cp /home/forge/mpa/deploy/forge-site.conf \
        /etc/nginx/forge-conf/$SITE_ID/site.conf
sudo nginx -t && sudo systemctl reload nginx
```

---

## Gotchas we hit (and the fixes)

These are documented inline in `forge-site.conf` and `nginx-mpa.conf`, but
collected here for searchability:

1. **`alias` + `index` directive = 500.** The outer Forge server block has
   `index index.html index.htm;`. nginx APPENDS `index.html` to any `alias`
   that points at a file, turning `example.html` into `example.htmlindex.html`.
   Error log: `"...example.htmlindex.html" is not a directory`.
   **Fix:** use `root` + `try_files` for the root location, not `alias`.

2. **Permission denied (403) on every request.** nginx runs as `www-data`,
   not `forge`. It needs `x` permission on every parent directory.
   **Fix:** `chmod o+x /home/forge /home/forge/mpa /home/forge/mpa/sdk`.

3. **`sudo tee <<HEREDOC` mangled in PowerShell.** Pasting a big heredoc
   through SSH+sudo sometimes truncates mid-stream. **Fix:** write the
   heredoc to a normal file in `~/` first (no sudo), then `sudo cp` it
   into place as a single atomic command. Or use `nano` interactively.

4. **`curl ... 2>&1 | head -3`** truncates the actual response body.
   **Fix:** drop the `2>&1` if you want to see the response — or use `-i`
   alone and let it print everything.

5. **Forge already provisions SSL.** Don't `certbot --nginx` over Forge's
   cert paths — go through the Forge dashboard for LetsEncrypt instead.
   The cert lives under `/etc/nginx/ssl/domains/<a>/<b>/`.

---

## Troubleshooting cheatsheet

| Symptom | Where to look |
|---|---|
| 500 on `/` | `sudo tail /var/log/nginx/<site-id>-error.log` — usually permissions or the alias+index bug |
| 502 on `/api/*` | `sudo journalctl -u mpa-backend -n 200` — backend crashed or not running |
| 403 on everything | Forgot the `chmod o+x` from Section 4 |
| Whisper or HF download stalls | `df -h` (disk full?), check `~/.cache/` writable |
| `ModuleNotFoundError` on startup | `pip install -r requirements.txt` not re-run after a pull |
| Ollama "model not found" | `ollama list`, then `ollama pull qwen3:1.7b` |
| CORS error in Seraph's console | `backend/main.py` has `allow_origins=["*"]` — should "just work". If you see CORS errors, paste them to me |
| Big request times out | Bump `proxy_read_timeout` in `forge-site.conf` |
| OOM (kernel kills the backend) | `free -h` to confirm; bump server RAM, or swap Whisper-medium for "small" in `backend/main.py` |

---

## Files in `deploy/`

| File | Use when | Goes where |
|---|---|---|
| `forge-site.conf` | **Forge-managed server (production)** | `/etc/nginx/forge-conf/<site-id>/site.conf` |
| `nginx-mpa.conf` | Plain nginx (non-Forge) | `/etc/nginx/sites-available/mpa.tomorrow.services` |
| `mpa-backend.service` | Any server, after Phase 2 | `/etc/systemd/system/mpa-backend.service` |
| `DEPLOY.md` | Reading | This file |

`backend/requirements.txt` is at the root of `backend/`.
