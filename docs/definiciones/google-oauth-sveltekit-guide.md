# Google OAuth 2.0 with SvelteKit (SPA) + FastAPI

A reference implementation guide for adding Google authentication to a SvelteKit SPA with a FastAPI backend. Derived from a production project (Hermes WebUI).

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Browser (SvelteKit SPA — adapter-static, ssr: false)      │
│                                                             │
│  Login Page ──► Google Consent Screen ──► Callback/Register │
│       │                                         │           │
│       └─── auth store (user, isAuthenticated) ◄─┘           │
│                                                             │
│  Every API call: fetch(url, { credentials: 'include' })     │
└──────────────────────────┬──────────────────────────────────┘
                           │ cookies (HttpOnly JWT)
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  FastAPI Backend                                             │
│                                                              │
│  /api/auth/google/login    → 302 → Google OAuth URL         │
│  /api/auth/google/callback → exchange code → set JWT cookie  │
│  /api/auth/register        → create user → set JWT cookie    │
│  /api/auth/me              → validate cookie → return user   │
│  /api/auth/logout          → clear cookie                    │
│                                                              │
│  Protected endpoints use: Depends(get_current_user)          │
└──────────────────────────────────────────────────────────────┘
```

**Key design choice**: The SvelteKit app is compiled as a **pure SPA** (`adapter-static` + `ssr: false`). There are NO SvelteKit server hooks — no `hooks.server.ts`, no server-side `load` functions. The frontend is served by FastAPI itself from the same origin, eliminating CORS issues entirely. All auth logic lives in FastAPI; the SPA only reads auth state client-side.

---

## Prerequisites

### Google Cloud Console

1. Create a project at [Google Cloud Console](https://console.cloud.google.com/).
2. Enable **Google+ API** / **Google Identity**.
3. Go to **APIs & Services → Credentials → Create OAuth 2.0 Client ID**.
4. Application type: **Web application**.
5. Add **Authorized redirect URI**: `https://your-domain.com/api/auth/google/callback`.
6. Note the **Client ID** and **Client Secret**.

### Environment Variables

```env
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxxxxxxxxxxxxxxx
JWT_SECRET=a-long-random-string-at-least-32-chars
JWT_EXPIRE_HOURS=24
APP_URL=https://your-domain.com
```

---

## Backend Implementation (FastAPI)

### 1. Configuration

Use `pydantic-settings` to load from environment:

```python
# backend/config.py
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    google_client_id: str
    google_client_secret: str
    jwt_secret: str
    jwt_expire_hours: int = 24
    app_url: str = "http://localhost:8080"

    class Config:
        env_file = ".env"

settings = Settings()
```

### 2. User Model

```python
# backend/models/user.py
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String
from backend.database import Base

class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    google_sub: Mapped[str] = mapped_column(String, unique=True, index=True)
    email: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    picture: Mapped[str | None] = mapped_column(String, nullable=True)
    hermes_profile: Mapped[str] = mapped_column(String, unique=True)
    created_at: Mapped[str] = mapped_column(String)
```

The `google_sub` is Google's unique user identifier (the `sub` claim from their ID token). It is **more stable than email** (users can change their Google email). Always use `google_sub` as the primary link between Google identity and your local user record.

### 3. Auth Router

This is the core of the implementation. Each endpoint handles one step of the flow:

```python
# backend/routers/auth.py
import base64, json, secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request, Response, Depends, HTTPException
from jose import jwt, JWTError, ExpiredSignatureError
from sqlalchemy import select

from backend.config import settings
from backend.database import AsyncSessionLocal
from backend.models.user import User

router = APIRouter(prefix="/api/auth", tags=["auth"])

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

# ─── Helpers ────────────────────────────────────────────────

def _make_jwt(user_id: str, email: str, name: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "name": name,
        "exp": datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expire_hours),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def _set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key="auth_token",
        value=token,
        httponly=True,
        secure=settings.app_url.startswith("https"),
        samesite="lax",
        max_age=settings.jwt_expire_hours * 3600,
        path="/",
    )


def _clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(key="auth_token", path="/")


async def get_current_user(request: Request) -> User:
    """FastAPI dependency: reads JWT cookie, returns User or raises 401."""
    token = request.cookies.get("auth_token")
    if not token:
        raise HTTPException(401, "Not authenticated")

    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except ExpiredSignatureError:
        raise HTTPException(401, "Token expired")
    except JWTError:
        raise HTTPException(401, "Invalid token")

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.id == payload["sub"])
        )
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(401, "User not found")
        return user

# ─── Endpoints ──────────────────────────────────────────────

@router.get("/google/login")
async def google_login(request: Request):
    """Step 1: Redirect browser to Google's OAuth consent screen."""
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": f"{settings.app_url}/api/auth/google/callback",
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
    }
    url = f"{GOOGLE_AUTH_URL}?{urlencode(params)}"
    return Response(status_code=302, headers={"Location": url})


@router.get("/google/callback")
async def google_callback(code: str, request: Request):
    """Step 2: Google redirects here after user consents.
    Exchange code → access token → user info.
    """
    # Exchange authorization code for access token
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "code": code,
                "redirect_uri": f"{settings.app_url}/api/auth/google/callback",
                "grant_type": "authorization_code",
            },
        )
        token_data = token_resp.json()
        access_token = token_data["access_token"]

        # Fetch user profile from Google
        user_resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        google_user = user_resp.json()

    google_sub = google_user["id"]
    email = google_user["email"]
    name = google_user["name"]
    picture = google_user.get("picture")

    # Check if user already exists
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.google_sub == google_sub)
        )
        existing_user = result.scalar_one_or_none()

    if existing_user:
        # Existing user → set JWT cookie, redirect to app
        token = _make_jwt(existing_user.id, existing_user.email, existing_user.name)
        response = Response(
            status_code=302,
            headers={"Location": f"{settings.app_url}/"},
        )
        _set_auth_cookie(response, token)
        return response

    # New user → redirect to registration page with profile data in state
    state_data = {
        "google_sub": google_sub,
        "email": email,
        "name": name,
        "picture": picture,
    }
    state_encoded = base64.urlsafe_b64encode(
        json.dumps(state_data).encode()
    ).decode()

    response = Response(
        status_code=302,
        headers={"Location": f"{settings.app_url}/register?state={state_encoded}"},
    )
    return response


@router.post("/register")
async def register(body: dict):
    """Step 3 (new users only): Create user account with chosen profile name."""
    import re

    hermes_profile = body.get("hermes_profile", "").strip()
    if not re.match(r"^[a-z0-9_-]{2,48}$", hermes_profile):
        raise HTTPException(400, "Invalid profile name")

    async with AsyncSessionLocal() as session:
        # Check uniqueness
        existing = await session.execute(
            select(User).where(User.hermes_profile == hermes_profile)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(409, "Profile name already taken")

        # Create user
        user_id = secrets.token_hex(16)
        user = User(
            id=user_id,
            google_sub=body["google_sub"],
            email=body["email"],
            name=body["name"],
            picture=body.get("picture"),
            hermes_profile=hermes_profile,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        session.add(user)
        await session.commit()

    # Set JWT cookie
    token = _make_jwt(user.id, user.email, user.name)
    response = Response(
        status_code=200,
        content=json.dumps({"user_id": user.id}),
        media_type="application/json",
    )
    _set_auth_cookie(response, token)
    return response


@router.get("/me")
async def me(user: User = Depends(get_current_user)):
    """Return current user info. Validates JWT cookie via dependency."""
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "picture": user.picture,
        "hermes_profile": user.hermes_profile,
    }


@router.post("/logout")
async def logout():
    """Clear the JWT cookie."""
    response = Response(status_code=200)
    _clear_auth_cookie(response)
    return response
```

### 4. Register the Router

```python
# backend/main.py
from backend.routers.auth import router as auth_router
app.include_router(auth_router)
```

---

## Frontend Implementation (SvelteKit SPA)

### 1. SPA Configuration

Since this is a **pure SPA** (no SSR, no server-side auth), configure SvelteKit for static output:

```typescript
// frontend/src/routes/+layout.ts
export const prerender = false;
export const ssr = false;
```

```javascript
// frontend/svelte.config.js
import adapter from '@sveltejs/adapter-static';

export default {
    kit: {
        adapter: adapter({
            pages: 'build',
            assets: 'build',
        }),
    },
};
```

### 2. API Client

The critical detail is `credentials: 'include'` — this ensures the HttpOnly JWT cookie is sent with every request:

```typescript
// frontend/src/lib/api/client.ts
export class ApiError extends Error {
    constructor(public status: number, message: string) {
        super(message);
    }
}

export async function api<T>(
    path: string,
    options: RequestInit = {}
): Promise<T> {
    const response = await fetch(path, {
        ...options,
        credentials: "include",   // ← sends HttpOnly cookies
        headers: {
            "Content-Type": "application/json",
            ...options.headers,
        },
    });

    if (response.status === 401) {
        throw new ApiError(401, "Unauthorized");
    }
    if (!response.ok) {
        throw new ApiError(response.status, await response.text());
    }
    return response.json();
}
```

### 3. Auth API Functions

```typescript
// frontend/src/lib/api/auth.ts
import { api } from "./client";

export interface UserInfo {
    id: string;
    email: string;
    name: string;
    picture: string | null;
    hermes_profile: string;
}

export function getGoogleLoginUrl(): string {
    return "/api/auth/google/login";
}

export async function getMe(): Promise<UserInfo> {
    return api<UserInfo>("/api/auth/me");
}

export async function logout(): Promise<void> {
    await api("/api/auth/logout", { method: "POST" });
}

export async function register(data: {
    google_sub: string;
    email: string;
    name: string;
    picture: string | null;
    hermes_profile: string;
}): Promise<{ user_id: string }> {
    return api("/api/auth/register", {
        method: "POST",
        body: JSON.stringify(data),
    });
}
```

### 4. Auth Store

Since these stores live in a `.ts` file (not `.svelte`), they use Svelte's `writable` stores, NOT runes (`$state`):

```typescript
// frontend/src/lib/stores/auth.ts
import { writable } from "svelte/store";
import type { UserInfo } from "$lib/api/auth";

export const user = writable<UserInfo | null>(null);
export const isAuthenticated = writable(false);
export const authLoaded = writable(false);
```

### 5. Root Layout — Auth Check on Load

This runs **once** when the app mounts. It checks if the JWT cookie is valid by calling `/api/auth/me`:

```svelte
<!-- frontend/src/routes/+layout.svelte -->
<script lang="ts">
    import { onMount } from "svelte";
    import { user, isAuthenticated, authLoaded } from "$lib/stores/auth";
    import { getMe } from "$lib/api/auth";

    onMount(async () => {
        try {
            const me = await getMe();
            $user = me;
            $isAuthenticated = true;
        } catch {
            $user = null;
            $isAuthenticated = false;
        } finally {
            $authLoaded = true;
        }
    });
</script>

<!-- Show loading state until auth check completes -->
{#if $authLoaded}
    <slot />
{:else}
    <div>Loading...</div>
{/if}
```

### 6. Login Page

A simple page with a link that triggers the server-side OAuth redirect:

```svelte
<!-- frontend/src/routes/login/+page.svelte -->
<script lang="ts">
    import { getGoogleLoginUrl } from "$lib/api/auth";
</script>

<a href={getGoogleLoginUrl()}>
    Sign in with Google
</a>
```

**How it works**: Clicking the link navigates to `/api/auth/google/login`. The FastAPI endpoint returns a `302` redirect to Google's consent screen. The browser follows the redirect natively — no JavaScript fetch involved. After the user consents, Google redirects back to `/api/auth/google/callback?code=...`.

### 7. Register Page (New Users)

When a new user completes Google OAuth, they're redirected here with their Google profile data encoded in the URL's `state` parameter:

```svelte
<!-- frontend/src/routes/register/+page.svelte -->
<script lang="ts">
    import { onMount } from "svelte";
    import { register } from "$lib/api/auth";
    import { page } from "$app/stores";

    let googleData: {
        google_sub: string;
        email: string;
        name: string;
        picture: string | null;
    } | null = null;

    let profileName = "";
    let error = "";
    let loading = false;

    onMount(() => {
        const state = $page.url.searchParams.get("state");
        if (!state) {
            error = "Missing registration data. Please try again.";
            return;
        }
        try {
            googleData = JSON.parse(atob(state));
        } catch {
            error = "Invalid registration data.";
        }
    });

    async function handleRegister() {
        if (!googleData) return;
        loading = true;
        error = "";

        try {
            await register({
                ...googleData,
                hermes_profile: profileName.trim(),
            });
            // Hard redirect — the layout's onMount will pick up the new cookie
            window.location.href = "/";
        } catch (e: any) {
            error = e.message || "Registration failed";
        } finally {
            loading = false;
        }
    }
</script>

{#if googleData}
    <form on:submit|preventDefault={handleRegister}>
        <p>Welcome, {googleData.name}!</p>
        <p>Choose a profile name:</p>
        <input
            type="text"
            bind:value={profileName}
            placeholder="e.g. john_doe"
            pattern="^[a-z0-9_-]{2,48}$"
            required
        />
        <button type="submit" disabled={loading}>
            {loading ? "Creating..." : "Create Account"}
        </button>
        {#if error}
            <p class="error">{error}</p>
        {/if}
    </form>
{:else if error}
    <p>{error}</p>
{:else}
    <p>Loading...</p>
{/if}
```

### 8. Route Protection (Client-Side)

Since this is a SPA with no server-side rendering, route protection happens at the component level:

```svelte
<!-- frontend/src/routes/+page.svelte -->
<script lang="ts">
    import { authLoaded, isAuthenticated } from "$lib/stores/auth";
</script>

{#if !$authLoaded}
    <div>Loading...</div>
{:else if !$isAuthenticated}
    {@html ""}
    <svelte:head>
        <meta http-equiv="refresh" content="0;url=/login" />
    </svelte:head>
    Redirecting to login...
{:else}
    <!-- Protected app content here -->
    <h1>Dashboard</h1>
{/if}
```

> **Note**: For more robust protection, use `goto("/login")` from `$app/navigation` inside an `$effect` that watches `$authLoaded && !$isAuthenticated`. A `meta refresh` or `window.location.href` also works since this is a SPA.

### 9. Logout

```typescript
// In any component (e.g., MenuBar.svelte)
import { logout } from "$lib/api/auth";

async function handleLogout() {
    await logout();
    window.location.href = "/login"; // Hard redirect clears state
}
```

---

## Complete Flow Diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│                        EXISTING USER                                 │
│                                                                      │
│  Browser            FastAPI                         Google           │
│    │                   │                               │             │
│    │ GET /login        │                               │             │
│    │──────────────────>│                               │             │
│    │<─ HTML ──────────│                               │             │
│    │                   │                               │             │
│    │ Click "Sign in"   │                               │             │
│    │ GET /api/auth/google/login                       │             │
│    │──────────────────>│                               │             │
│    │                   │── 302 Redirect ──────────────>│             │
│    │<──────────────────│───────────────────────────────│             │
│    │ User consents     │                               │             │
│    │<─ Redirect with code ────────────────────────────│             │
│    │                   │                               │             │
│    │ GET /api/auth/google/callback?code=XXX            │             │
│    │──────────────────>│                               │             │
│    │                   │── Exchange code for token ───>│             │
│    │                   │<── Access token ──────────────│             │
│    │                   │── Fetch user info ───────────>│             │
│    │                   │<── {sub, email, name} ────────│             │
│    │                   │                               │             │
│    │                   │ User exists in DB              │             │
│    │                   │ Create JWT, set cookie         │             │
│    │<─ 302 / (Set-Cookie: auth_token=eyJ...) ─────────│             │
│    │                   │                               │             │
│    │ GET /             │                               │             │
│    │  onMount: GET /api/auth/me                        │             │
│    │  (cookie sent automatically)                      │             │
│    │──────────────────>│                               │             │
│    │<─ {user info} ───│                               │             │
│    │ App is ready!     │                               │             │
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│                        NEW USER                                      │
│                                                                      │
│  (Same OAuth flow until callback)                                    │
│    │                   │                               │             │
│    │                   │ User NOT in DB                 │             │
│    │<─ 302 /register?state=base64(google_data) ───────│             │
│    │                   │                               │             │
│    │ User fills form, picks profile name               │             │
│    │ POST /api/auth/register                           │             │
│    │──────────────────>│                               │             │
│    │                   │ Create user in DB              │             │
│    │                   │ Create JWT, set cookie         │             │
│    │<─ 200 (Set-Cookie: auth_token=eyJ...) ───────────│             │
│    │                   │                               │             │
│    │ window.location.href = "/"                        │             │
│    │  onMount: GET /api/auth/me → success              │             │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Security Considerations

| Aspect | Implementation | Why |
|---|---|---|
| **Token storage** | HttpOnly cookie | JavaScript cannot read or steal the token (XSS protection) |
| **SameSite** | `Lax` | Cookie is sent on top-level navigations (OAuth redirects work), but not on cross-site POST requests (CSRF protection) |
| **Secure flag** | Auto-set when `APP_URL` uses `https` | Cookie only sent over HTTPS in production |
| **JWT expiry** | 24h (configurable) | Limits window if token is compromised |
| **google_sub over email** | Uses Google's `sub` as primary identifier | Emails can change; `sub` is permanent per Google account |
| **No CORS** | Same-origin (FastAPI serves SPA) | Eliminates an entire class of security complexity |
| **State parameter** | Base64-encoded Google profile in URL | Passes OAuth data to registration page without setting a pre-auth cookie |
| **No token refresh** | User must re-auth via Google after expiry | Simplicity trade-off; avoids refresh token management |

---

## Checklist for a New Project

- [ ] Create Google OAuth 2.0 credentials in Google Cloud Console
- [ ] Set redirect URI to `{your_app_url}/api/auth/google/callback`
- [ ] Add env vars: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `JWT_SECRET`, `APP_URL`
- [ ] Backend: implement the 5 auth endpoints (`login`, `callback`, `register`, `me`, `logout`)
- [ ] Backend: add `get_current_user` dependency for protected routes
- [ ] Frontend: configure `adapter-static` with `ssr: false`
- [ ] Frontend: ensure all API calls use `credentials: 'include'`
- [ ] Frontend: check auth state in root layout (`onMount` → `getMe()`)
- [ ] Frontend: protect routes client-side (redirect to `/login` if not authenticated)
- [ ] Frontend: login page links to `/api/auth/google/login` (not a JS fetch)
- [ ] Frontend: logout clears cookie server-side, then hard-redirects

---

## Common Pitfalls

1. **`credentials: 'include'` missing**: The JWT cookie won't be sent. Every `fetch` call must include it.
2. **Using `fetch` for the OAuth login**: The login button must be a regular `<a href>` or `window.location` — Google OAuth requires a full browser redirect, not an AJAX call.
3. **`ssr: true` in SvelteKit**: Server-side rendering won't have access to the HttpOnly cookie in the same way. If you need SSR, use a different auth strategy (server-side sessions or Bearer tokens).
4. **Forgetting the `/` path on cookie**: If you set the cookie without `path=/`, it may only be sent for requests under the current path prefix.
5. **Email as primary identifier**: Google users can change their email. Always use `google_sub` (the `sub` claim) to link identities.
6. **Stores in `.ts` files**: Svelte runes (`$state`, `$effect`) only work in `.svelte` and `.svelte.ts` files. Plain `.ts` store files must use `writable`/`derived` from `svelte/store`.
