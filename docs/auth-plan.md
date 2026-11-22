# Auth plan: Google + email magic link

Status (2026-11-02): all three phases are built. The PR-by-PR list is under "Where it stands" below. Phase 1 shipped in
#50 (2026-10-31) with the answers below folded in: admin by account (`ADMIN_EMAILS`) and a preferences page (theme, default
sportsbook, Best props toggles) came in phase 1, and new accounts confirm 21+ once.
Changes from the draft: no Authlib. The Google flow is ~60 lines on `requests`; the state and nonce sit in a short-lived cookie,
and the ID token comes straight from Google's token endpoint, so its claims are checked but not its signature (OIDC Core
3.1.3.7). Auth routes live under `/api/auth/` (`/api/auth/prefs` instead of `/api/me/prefs`).

## Goals and non-goals

- People can sign in with **Google** or with a **one-time link / 6-digit code sent by email**. Both lead to the same account
  when the verified email matches.
- Signing in stays **optional**: every board stays public. Phase 1 only adds accounts; phase 2 gives them something to do
  (the betslip and preferences follow you between devices).
- No passwords are stored, ever. No Facebook.
- The admin password (`ADMIN_TOKEN`, Bearer header) kept working in phase 1. Phase 3 replaced it with "signed in as an
  email in `ADMIN_EMAILS`" and retired the password (#57).

## How it fits the current app

- The API and the React build are served from **one origin** (one container on Railway; Vite proxies `/api` in dev), so a
  plain **httpOnly session cookie** works and no JWTs or tokens live in JavaScript or localStorage.
- `admin_writes_only` in `backend/app/main.py` rejects **every** non-GET request without the admin token. The new
  `/api/auth/*` (and later `/api/me/*`) POSTs must be exempted, and they need their own CSRF check, because a cookie is
  sent automatically (the Bearer admin header is not).
- New tables are created by `Base.metadata.create_all`, so no `ensure_columns` entries are needed. Works on SQLite locally and
  on prod Postgres.
- One replica (`.railway/railway.ts`), so in-memory rate limiting, like the existing `_FAILS` for admin guesses, is enough.
- The service worker (`frontend/pwa/`) already skips `/api/`, so auth responses are never cached.
- **PWA catch:** on iOS an installed PWA does not share cookies with Safari. A magic link tapped in Mail opens in Safari and
  signs *Safari* in, not the home-screen app. That's why the email also carries a **6-digit code** to type into the app.

## Data model (`backend/app/db.py`)

```text
users
  id               int pk
  email            varchar(254) unique, stored lowercased
  email_verified   bool        (true for magic-link and for Google when Google says email_verified)
  google_sub       varchar(64) unique, nullable   (Google's stable user id; emails can change)
  name             varchar(120) nullable
  avatar_url       varchar(500) nullable
  created_at, last_login_at

sessions
  id               varchar(64) pk  = sha256(cookie token), so a DB leak exposes no usable cookies
  user_id          fk users ON DELETE CASCADE, indexed
  created_at, expires_at, last_seen_at
  user_agent       varchar(200)   (for a future "signed-in devices" list)

login_tokens       (magic links / codes)
  id               int pk
  email            varchar(254), indexed
  token_hash       varchar(64) unique   sha256 of the link token
  code_hash        varchar(64)          sha256(email + 6-digit code)
  next_path        varchar(200)         where to land after sign-in (path only, validated)
  created_at, expires_at (15 min), used_at nullable
  attempts         int                  wrong code guesses; dead after 5
```

## Backend

New module `backend/app/auth.py` with an `APIRouter(prefix="/api/auth")`, included from `main.py` like `nba_routes`.

### Sessions

- On sign-in: 32 random bytes (`secrets.token_urlsafe(32)`) → cookie `sid`; store `sha256` in `sessions`.
- Cookie: `HttpOnly; Secure` (not on localhost); `SameSite=Lax; Path=/; Max-Age=30 days`.
- Sliding expiry: `current_user` pushes `expires_at` forward when `last_seen_at` is more than a day old (one write a day,
  not one per request).
- `current_user(request, db) -> User | None` dependency, plus `require_user` that raises 401.
- Sign-out deletes the row and clears the cookie. A daily scheduler job deletes expired sessions and login tokens.

### CSRF

The cookie is `SameSite=Lax`, which already stops cross-site POSTs in current browsers. As a second layer, every
cookie-authenticated non-GET checks that `Origin` (or `Referer`) is `PUBLIC_URL` or a `CORS_ORIGINS` entry.
`api.ts` already sends everything through one fetch wrapper, so it can also add an `X-Requested-With` header.

### Google (OpenID Connect, authorization code flow)

Use **Authlib** (`authlib` + `itsdangerous` for Starlette's `SessionMiddleware`, which holds the `state` and `nonce` for the
few seconds of the round trip).

- `GET /api/auth/google/start?next=/props` → redirect to Google with scopes `openid email profile`.
- `GET /api/auth/google/callback` → exchange the code, verify the ID token (Authlib checks the signature, `aud`, `iss`, `nonce`),
  then:
  1. look up the user by `google_sub`; if none,
  2. look up by email **only if `email_verified` is true**, and attach `google_sub` (account linking); if none,
  3. create the user.
  Then start a session and redirect to `next` (validated: must start with `/`, not `//`).
- On error or cancel: redirect to `/?signin=failed`.

### Email magic link + code

- `POST /api/auth/email {email, next}`:
  - Normalize and validate the address; **always answer 200** "check your email" so nobody can test which addresses have
    accounts.
  - Rate limits: 3 per email per 15 min, 10 per IP per hour (the same in-memory pattern as `_FAILS`).
  - Create a `login_tokens` row; send one email with the link `PUBLIC_URL/signin/verify#t=<token>` and a 6-digit code.
- **Link scanners:** Outlook/Defender and some corporate filters *GET* every link in an email, which would burn a token
  consumed on GET. So the link opens a **frontend page** that POSTs the token (the token sits in the `#fragment`, which never
  reaches the server or its logs). Scanners don't run JavaScript or POST.
- `POST /api/auth/email/verify {token}` or `{email, code}`: check unused, unexpired, attempts < 5 (count a wrong code);
  mark used; find or create the user by email (`email_verified = true`); start a session.
- **Sending:** Resend's HTTP API through `requests` (already a dependency; no SDK). Free tier: 3,000 emails/month. It needs
  SPF/DKIM DNS records on jdhaber.com, sending from e.g. `signin@jdhaber.com`. In dev with no key set, print the link and
  code to the console instead of sending.

### Other endpoints

- `GET /api/auth/me` → `{email, name, avatar_url}` or `null` (200 either way, so the frontend needs no special case).
- `POST /api/auth/logout`.
- `DELETE /api/auth/me` deletes the account and its sessions. Google's consent screen asks for a privacy policy, and people
  expect to be able to delete their account.

### Middleware change (`main.py`)

```python
PUBLIC_WRITES = ("/api/auth/",)   # later: "/api/me/"
if request.method not in ("GET", "HEAD", "OPTIONS") and not request.url.path.startswith(PUBLIC_WRITES):
    ...existing admin check...
```

Those routes do their own CSRF check, rate limiting and `require_user`.

### Config (`config.py`, Railway variables)

| Variable | Purpose |
|---|---|
| `PUBLIC_URL` | `https://process.jdhaber.com`; used to build links and the redirect URI (never trust `Host`) |
| `SESSION_SECRET` | signs Starlette's short-lived OAuth state cookie |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Google Cloud OAuth client |
| `RESEND_API_KEY`, `EMAIL_FROM` | magic-link email; unset → log to the console |
| `ADMIN_EMAILS` | phase 3 |

`requirements.txt`: add `authlib`, `itsdangerous`.

## Frontend

- `auth.ts`: keep the admin token functions; add `useMe()` (TanStack Query on `/api/auth/me`, `staleTime` of a few minutes,
  invalidated on sign-in and sign-out).
- **Sign-in sheet** (reuse `components/Sheet.tsx`): a "Continue with Google" button (a plain link to
  `/api/auth/google/start?next=<current path>`; follow Google's button branding rules), an "or" divider, then an email
  field → "Email me a sign-in link" → a "Check your email" step with the 6-digit code input and "resend" (after 30s).
- `/signin/verify` route: reads `#t=`, POSTs it, shows "Signed in" and returns to `next`, or "That link expired or was
  used" with a button to send a new one.
- `TopBar` / `Menu`: a "Sign in" button when signed out; the avatar or initial with "Signed in as …", "Sign out" and
  "Delete account" when signed in.
- Handle `?signin=failed` with a toast.

## Google Cloud setup (one-time, by you)

1. Google Cloud console → new project → **OAuth consent screen**: External, app name, support email, logo optional,
   authorized domain `jdhaber.com`, links to the privacy policy (and terms) pages. Scopes `openid email profile` are
   non-sensitive, so Google won't need to verify the app.
2. **Publish** the app ("In production"). In "Testing" only up to 100 listed test users can sign in.
3. Credentials → OAuth client ID → Web application. Redirect URIs:
   `https://process.jdhaber.com/api/auth/google/callback` and `http://localhost:8000/api/auth/google/callback`
   (and `http://localhost:5173/...` if testing through the Vite proxy).
4. Put the client ID and secret in Railway variables and `backend/.env`.

You'll also need a short **privacy policy** page: what's stored (email, name, avatar, sessions), why, and how to delete it.

## Tests (`backend/tests/test_auth.py`)

`TestClient` reports its host as `testclient`, which is in `LOCAL_HOSTS`, so it passes the admin check. Tests for the
middleware exemption must fake a remote client.

- Magic link: request → token stored hashed → verify → cookie set → `/me` returns the user; reuse fails; expired fails;
  5 wrong codes kill the token; unknown and known emails get the same response; rate limits.
- Google: patch Authlib's token exchange to return a canned ID token; new user, existing by `sub`, linking by verified email,
  **no** linking when `email_verified` is false.
- Sessions: logout invalidates; expired session → `/me` null; a cross-origin `Origin` on a cookie POST → 403.
- Non-auth POSTs from a remote client are still admin-only.
- Frontend: vitest for `next`-path validation and the fragment parsing on the verify page.

## Phases

1. **Accounts** (this plan): tables, both sign-in methods, `/me`, sign-in UI, middleware exemption, tests, privacy page.
   About one focused PR.
2. **Make accounts useful**: `/api/me/slip` and `/api/me/prefs` (sportsbook, league, one-per-player, state). The betslip
   (`nfl-props-slip-v1` in localStorage) merges into the server copy on first sign-in, then syncs. Later: saved picks or a
   bet tracker.
3. **Admin via account**: `is_admin` is also true for a signed-in user in `ADMIN_EMAILS`; retire typing the admin password
   once that works.

## Where it stands (2026-11-02)

**Phase 1: accounts.**
- **#50:** Google and email-link/code sign-in, the 21+ check, httpOnly cookie sessions, the preferences page, admin by
  account, and the privacy policy.
- **#51:** Model check and Admin shown only to the owner, with the grades endpoints admin-only.
- **#52:** Sign out also forgot the old admin password. Best props and Parlays open at your sportsbook.

**Phase 2: make accounts useful.**
- Preferences follow the account (#50). Settings later added to the same `/api/auth/prefs`:
  - your teams and favorite players (#54)
  - the main tabs' order (#56)
- **The betslip follows the account (#58):**
  - legs and stakes only; the betting state and "all books" stay per device
  - `GET`/`PUT /api/me/slip`, where a stale save gets a 409 and is merged
  - first sign-in merges a signed-out slip
- **The bet tracker:** `docs/bet-tracker-plan.md`, #53 and #59.
- **Who's Hot (#55):** a public tab, not an account feature, but built in the same stretch.
- **Not done: the league in the account.** It stays in the browser, since the URL already says which league you're in.

**Phase 3: admin via account.**
- **Admin by account:** #50.
- **The password retired (#57):** admin is now the `ADMIN_EMAILS` account or a request from the host itself (a laptop
  running the app, or a `railway ssh` shell, the way back in). `ADMIN_TOKEN` can be deleted from Railway once #57 is live.

**Next, by choice:** email alerts, the tracker plan's phase 3. Resend is set up, and favorites and Who's Hot give people
things to hear about. They need an opt-in on Preferences and an unsubscribe link in every email.

## Open questions for you

- What should signing in unlock first: slip/prefs sync (phase 2), or something new like tracked bets or alerts?
- Answer: Let's start with only having the admin page accessible to my account as well as a preferences page for default sportsbook, theme, etc.
- Do you want an age confirmation ("I'm 21+") at sign-up, given the betting focus? `ResponsibleGaming.tsx` already exists.
- Answer: Yes
- Resend vs. Postmark for email: either works; Resend is simpler to start with.
- Answer: Resend. The test key is in backend/.env (git-ignored), not in this doc.
