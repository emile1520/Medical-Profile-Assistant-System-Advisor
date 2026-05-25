# Seraph ↔ MPA authentication: two options

The MPA SDK already passes `Authorization: Bearer <token>` and `X-User-Id`
headers on every request. The backend just doesn't *verify* the token yet.
This doc presents two ways to wire up real authentication so only Seraph can
call the MPA API.

## The constraint

The SDK is JavaScript served from `https://mpa.tomorrow.services/sdk/mpa-sdk.js`.
Anything embedded directly in that file (or in the Seraph page) is visible to
any browser user. Therefore:

- **The real secret must live on Seraph's server**, never in browser JS.
- The browser can hold a short-lived, single-purpose **token** derived from
  that secret.

That rules out "ship a hard-coded API key in the SDK." It does *not* rule out
"the SDK carries a token issued per session by Seraph's backend."

---

## Option 1 — Short-lived JWT signed with a shared secret (RECOMMENDED)

### How it flows

```
[Seraph backend]                      [Browser / SDK]                  [MPA backend]
 1. shared secret MPA_JWT_SECRET (env var, only on these two servers)
 2. User opens a Seraph page
       │
       ▼
 3. Seraph signs a JWT
    payload = {sub:userId, clinic:clinicId, exp:now+30min}
    secret  = MPA_JWT_SECRET
       │
       │  injects jwt into page
       ▼
                     4. MPA.init({ token: jwt, userId: ... })
                                │
                                │  POST /upload-audio
                                │  Authorization: Bearer <jwt>
                                ▼
                                                            5. Verify jwt with
                                                               MPA_JWT_SECRET.
                                                               If sig OK and not
                                                               expired → accept.
                                                               Otherwise → 401.
```

The browser never sees `MPA_JWT_SECRET`. If a token leaks, it expires in 30
minutes. Anyone else trying to call MPA has no valid signed token.

### Seraph backend — issue the token

Seraph is presumably PHP/Laravel (USJ stack). If so:

```php
// composer require firebase/php-jwt
use Firebase\JWT\JWT;

$payload = [
    'sub'    => $user->id,
    'clinic' => $user->clinic_id,
    'iat'    => time(),
    'exp'    => time() + 60 * 30,   // 30 minutes
    'iss'    => 'seraph',
    'aud'    => 'mpa',
];

$jwt = JWT::encode($payload, env('MPA_JWT_SECRET'), 'HS256');

// Pass to the page (Blade example):
return view('consult', ['mpaToken' => $jwt]);
```

Then in the Blade template:

```html
<script src="https://mpa.tomorrow.services/sdk/mpa-sdk.js"></script>
<script>
  const mpa = MPA.init({
    token:  "{{ $mpaToken }}",
    userId: "{{ auth()->id() }}"
  });
</script>
```

If Seraph is Node/Express instead, the JWT library is `jsonwebtoken`:

```js
import jwt from 'jsonwebtoken';
const token = jwt.sign(
  { sub: user.id, clinic: user.clinicId, iss: 'seraph', aud: 'mpa' },
  process.env.MPA_JWT_SECRET,
  { algorithm: 'HS256', expiresIn: '30m' }
);
```

### MPA backend — verify on every request

```python
# requirements.txt: add pyjwt
import os, jwt
from fastapi import Header, HTTPException, Depends

MPA_JWT_SECRET = os.environ["MPA_JWT_SECRET"]

def verify_seraph_token(authorization: str = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token")
    token = authorization[len("Bearer "):]
    try:
        claims = jwt.decode(
            token,
            MPA_JWT_SECRET,
            algorithms=["HS256"],
            issuer="seraph",
            audience="mpa",
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired")
    except jwt.InvalidTokenError as e:
        raise HTTPException(401, f"Invalid token: {e}")
    return claims  # {sub, clinic, iat, exp, iss, aud}

# Use as a dependency on every protected route:
@app.post("/upload-audio")
async def upload_audio(
    file: UploadFile = File(...),
    claims: dict = Depends(verify_seraph_token),
):
    user_id   = claims["sub"]
    clinic_id = claims.get("clinic")
    # ... rest unchanged
```

### CORS lockdown (also do this)

Replace `allow_origins=["*"]` in `backend/main.py`:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://seraph.tomorrow.services",   # production
        "https://seraph-staging.tomorrow.services",
        "http://localhost:5173",              # local dev only — drop later
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "X-User-Id", "Content-Type"],
)
```

CORS stops other websites' browser pages from calling MPA on behalf of users.
JWT verification stops everyone else, including raw `curl` from any host.

### How to generate `MPA_JWT_SECRET`

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Set it on both servers:

```bash
# On the MPA box (Forge → Environment for the site, or systemd unit):
MPA_JWT_SECRET=<paste>

# On Seraph's box:
MPA_JWT_SECRET=<same value>
```

### What you get

- Browser never holds the actual secret.
- Tokens expire automatically (limited blast radius).
- MPA knows *which* user and *which* clinic on every call — useful for logs,
  rate-limits, and future per-tenant features.
- SDK code unchanged — it already passes `token`.

### What it costs

- ~30 lines of new code on each backend.
- Both servers need to share `MPA_JWT_SECRET` (one env var).
- Seraph backend needs an endpoint or page-render step that mints the token.

---

## Option 2 — Static API key + strict CORS (simpler v1)

A single shared key, set as an env var on both sides. MPA accepts only that
key.

### Variant 2a: key embedded in the Seraph page (LEAKY — not recommended)

```html
<script>
  const mpa = MPA.init({
    token:  "sk_live_abc123...",   // visible to anyone who opens DevTools
    userId: "dentist_42"
  });
</script>
```

Any user logged into Seraph can copy the key from the network tab and call MPA
from anywhere. **Don't do this in production.** Only acceptable for a closed
demo on a network you trust.

### Variant 2b: Seraph backend proxies the call (more secure but more work)

```
[Browser]  ──POST /seraph/api/mpa/upload──▶  [Seraph backend]
                                              │ adds X-MPA-Key: <secret>
                                              ▼
                                              [MPA backend]
```

The browser never sees the key. But this requires:
- A proxy endpoint on Seraph for every MPA endpoint (`/upload-audio`,
  `/sdk/process-procedure`).
- Streaming binary audio through Seraph (extra latency, bandwidth on Seraph's
  server).
- Reworking the SDK so `apiBase` points at Seraph, not MPA.

### MPA backend — verify key

```python
import os
from fastapi import Header, HTTPException, Depends

MPA_API_KEY = os.environ["MPA_API_KEY"]

def verify_api_key(authorization: str = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token")
    presented = authorization[len("Bearer "):]
    if not hmac.compare_digest(presented, MPA_API_KEY):  # constant-time compare
        raise HTTPException(401, "Invalid API key")
    return True

@app.post("/upload-audio")
async def upload_audio(
    file: UploadFile = File(...),
    _: bool = Depends(verify_api_key),
    x_user_id: str = Header(default=None),
):
    ...
```

(Same CORS lockdown applies as in Option 1.)

### What you get

- Trivially simple: one env var, one header check.

### What it costs

- Either the key leaks (variant 2a) or you have to build a proxy layer on
  Seraph (variant 2b) — which is more work than Option 1.
- No per-user / per-clinic identity in the token — MPA can't tell which
  dentist is calling.
- Rotating the key means coordinated env-var change on both servers + restart.

---

## Recommendation

**Option 1 (JWT)** is both more secure *and* less total work than Option 2b,
because the SDK already passes a `token` per session. Option 2a is the only
"easier" path and it's not actually safe.

If you want a middle ground for a quick demo: ship Option 2a for the very
first Seraph integration test, then upgrade to Option 1 before going live.

## Concrete next steps if you choose Option 1

1. Generate `MPA_JWT_SECRET` once, share it with Tomorrow Services securely
   (1Password, encrypted message — *not* email or Slack DM).
2. On MPA: add `pyjwt` to `backend/requirements.txt`, add the
   `verify_seraph_token` dependency, attach it to `/upload-audio`,
   `/sdk/process-procedure`, and any other protected routes.
3. On MPA: set `MPA_JWT_SECRET` in the systemd unit `Environment=` line.
4. On MPA: tighten CORS to Seraph's origin(s).
5. On Seraph: add the JWT-mint step on whatever page embeds the SDK.
6. End-to-end test: open Seraph page, confirm SDK works. Then try the same
   request from `curl` without the JWT — should get 401.
