# nabu_browser_session

Shared **unofficial** browser-login library for Nabux social and messaging MCP gateways (Instagram, WhatsApp, Snapchat, Facebook, Threads, X, LinkedIn, TikTok).

Each gateway keeps its official API path. This package is the browser path beside it. It owns the pieces every gateway would otherwise copy: the browser, the encrypted session vault, relogin signalling, human login capture, slow pacing, and media downloads. A gateway supplies a thin platform adapter.

This is not affiliated with those platforms. The person who connects an account owns it and accepts the platform terms, plus the risk of checkpoints or bans. Actions are paced slowly on purpose. A dead session raises `NeedsReloginError`. It does not come back as an empty success.

## Install

Production gateways pin the tag:

```text
nabu_browser_session @ git+https://github.com/NabuxAi/nabu-browser-session@v0.1.0
```

```bash
pip install "git+https://github.com/NabuxAi/nabu-browser-session@v0.1.0"
```

Tracking `main` (do not do this in production):

```bash
pip install "git+https://github.com/NabuxAi/nabu-browser-session@main"
```

Lightpanda is optional. The Python extra declares that choice; the Lightpanda binary itself is external and is reached over CDP.

```text
nabu_browser_session[lightpanda] @ git+https://github.com/NabuxAi/nabu-browser-session@v0.1.0
```

Python 3.11+. Playwright is pinned (`playwright==1.65.0`). Chromium is installed in the image, not by `pip`.

## Browser install

Copy [`docker/Dockerfile.snippet`](docker/Dockerfile.snippet) into the gateway image. The line that matters:

```bash
python -m playwright install --with-deps chromium
```

`--with-deps` installs Chromium's system libraries on Debian/Ubuntu. Run it as root before dropping privileges. The gateway `/data` volume should be `SESSION_DIR` (the default is `$DATA_DIR/browser-sessions`, and the other gateways already set `DATA_DIR=/data`).

## Environment

| Variable | Default | Role |
|---|---|---|
| `BROWSER_ENGINE` | `chromium` | `chromium` or `lightpanda` |
| `LIGHTPANDA_CDP_URL` | unset | CDP URL, for example `ws://127.0.0.1:9222`. Required for Lightpanda to actually be used |
| `NABU_BROWSER_HEADLESS` | `1` | `1`/`0`. Login capture uses a screenshot either way |
| `SESSION_DIR` | `$DATA_DIR/browser-sessions`, or `./.nabu-sessions` | Directory for the vault. Point this at the gateway volume |
| `SESSION_DB_PATH` | `$SESSION_DIR/sessions.db` | SQLite file. Set this to share an existing data directory |
| `NABU_SESSION_FERNET_KEY` | required to read or write a session | Fernet key. `FERNET_KEY` is accepted only when the primary variable is unset |
| `NABU_RATE_ENABLED` | `1` | Leave on in production |
| `NABU_RATE_CAPACITY` | `3` | Token bucket size per account |
| `NABU_RATE_REFILL_PER_MINUTE` | `3` | Refill rate per account |
| `NABU_RATE_MIN_INTERVAL_SECONDS` | `12` | Minimum gap between actions for one account |
| `NABU_RATE_JITTER_SECONDS` | `4` | Random extra delay, uniform from 0 to this value |
| `NABU_LOGIN_TIMEOUT_SECONDS` | `300` | How long a human login view stays open |
| `NABU_LOGIN_MAX_CAPTURES` | `4` | Open login browsers on one worker |
| `NABU_DOWNLOAD_MAX_BYTES` | `104857600` | Cap for one media file (100 MiB) |

Generate a key once and keep it next to the database. Changing it makes stored sessions unreadable, and the vault flags them `needs_relogin`:

```bash
python -c "from nabu_browser_session import generate_fernet_key; print(generate_fernet_key())"
```

`python -m nabu_browser_session` prints the resolved engine for each capability. It says whether the Fernet key and the CDP URL are set. It does not print them.

## Engines

Chromium (Playwright) is the default and the only engine for login, upload, and send.

`BROWSER_ENGINE=lightpanda` is used for reads and downloads when `LIGHTPANDA_CDP_URL` is set. Login, upload, and send fall back to Chromium before Lightpanda is contacted. If Lightpanda fails to connect, or the operation raises `EngineUnavailable` before a side effect, the same operation runs once on Chromium. Other exceptions are not retried, so a send is not performed twice.

One browser, one context per account session, closed when the action finishes. Login capture keeps that context open until the owner finishes, cancels, or the view expires. That in-flight browser lives in the worker that opened it.

## Public API

```python
from nabu_browser_session import (
    BrowserSessionService,   # construct once per gateway process
    PlatformAdapter,         # subclass this in the gateway
    StubPlatformAdapter,     # worked example, not a real platform
    LoginSelectors,
    LoginView,
    render_login_fragment,   # HTML a dashboard route can return
    Settings,
    SessionVault,
    SessionKey,
    SealedState,             # str() is masked; call reveal() for the browser
    EnvKeySource,
    StaticKeySource,
    generate_fernet_key,
    SessionLifecycle,
    SessionCallbacks,
    SessionEvent,
    SessionNotice,
    HealthResult,
    RateLimiter,
    PaceTicket,
    Clock,
    Capability,              # LOGIN, UPLOAD, SEND, READ, DOWNLOAD
    EngineKind,              # CHROMIUM, LIGHTPANDA
    EngineSelector,
    EngineChoice,
    BrowserEngine,
    ChromiumEngine,
    LightpandaEngine,
    open_engine,
    run_with_engine,
    LivePage,
    capture_live_page,
    page_url,
    storage_state_of,
    download_media,
    DownloadResult,
    cookies_from_storage,
    safe_filename,
    unofficial_tool_name,    # instagram_unofficial_profile
    unofficial_disclaimer,
    UNOFFICIAL_ACTIONS,
    UNOFFICIAL_DISCLAIMER,
    mask_session,
    mask_secret,
    mask_account,
    NeedsReloginError,
    ConfigurationError,
    EngineUnavailable,
    LoginCaptureError,
    DownloadError,
    AdapterNotImplemented,
    NabuBrowserError,
)
```

`SealedState`, `SessionRecord`, and the log lines never include cookie values, storage values, or the Fernet token. `reveal()` is the only way to hand state to the browser.

## Wire a gateway

Official tools stay `{platform}_{action}` (`instagram_profile`). Unofficial tools are `{platform}_unofficial_{action}` (`instagram_unofficial_profile`). The full sketch is [`examples/wire_gateway.py`](examples/wire_gateway.py):

```python
from fastmcp import FastMCP

from nabu_browser_session import (
    BrowserSessionService,
    StubPlatformAdapter,
    unofficial_tool_name,
)

mcp = FastMCP("example-gateway")
sessions = BrowserSessionService.from_env()
adapter = StubPlatformAdapter()  # replace with the platform adapter


@mcp.tool(name=unofficial_tool_name(adapter.platform, "session_status"))
async def session_status(owner_id: str, account: str) -> dict:
    """UNOFFICIAL browser session status. Not an official platform API."""
    return sessions.status(adapter, owner_id, account)


@mcp.tool(name=unofficial_tool_name(adapter.platform, "login_start"))
async def login_start(owner_id: str, account: str) -> dict:
    """UNOFFICIAL. The owner finishes QR or password login. The password is not stored."""
    view = await sessions.login.start(adapter, owner_id=owner_id, account=account)
    return view.public_dict()


@mcp.tool(name=unofficial_tool_name(adapter.platform, "profile"))
async def profile(owner_id: str, account: str) -> dict:
    """UNOFFICIAL slow-paced profile read."""
    return await sessions.profile(adapter, owner_id, account)
```

A dashboard route in `app/web.py` can render the capture without taking a dependency on a particular web framework:

```python
from fastapi.responses import HTMLResponse
from nabu_browser_session import render_login_fragment

@app.get("/unofficial/login/{capture_id}")
async def unofficial_login(capture_id: str):
    view = await sessions.login.refresh(capture_id)
    return HTMLResponse(render_login_fragment(view))
```

`refresh` checks `adapter.detect_login_success(page)`. When that returns true, the cookies and origin storage are encrypted and the browser closes. `view.needs_relogin` is false only after that save. `login.interact(capture_id, action="fill", selector=..., text=...)` forwards a keystroke the owner just typed. The text is not logged and not written to the vault.

The standard action names are `login_start`, `login_refresh`, `session_status`, `profile`, `list`, `send`, and `download`. Descriptions should include `unofficial_disclaimer()`.

## Adapter

```python
from nabu_browser_session import PlatformAdapter

class InstagramBrowserAdapter(PlatformAdapter):
    platform = "instagram"
    login_url = "https://www.instagram.com/accounts/login/"

    async def detect_login_success(self, page) -> bool: ...
    async def fetch_profile(self, page) -> dict: ...
    async def list_items(self, page, *, kind: str, limit: int = 20) -> list[dict]: ...
    async def send_message(self, page, *, recipient: str, text: str) -> dict: ...
    async def download_targets(self, page, *, target: str) -> list[str]: ...
```

`page.url` is a string, matching Playwright. `probe_url` defaults to `login_url`. Override it when the logged-in landing page is different. Raise `NeedsReloginError` when the platform shows a checkpoint. Raise `EngineUnavailable` only when the current engine cannot drive the flow and it has not performed the side effect yet (that is what triggers the Chromium retry). Do not return an empty list or `{"ok": true}` for a failure.

`StubPlatformAdapter` is the same shape pointed at `https://example.test/login`. Its send and download primitives raise `AdapterNotImplemented`.

## Sessions

The vault key is `(owner_id, platform, account)`. The SQLite row stores a Fernet token plus non-secret columns: status (`absent`, `active`, `needs_relogin`, `logged_out`), the flag, and cookie/origin counts. `SessionVault` takes a `KeySource` (`EnvKeySource` or `StaticKeySource`) so a gateway can wrap the secret it already has.

`status()` is read-only. An absent account comes back `needs_relogin: true` so the dashboard can prompt. `health_check`, `profile`, `list_items`, `send`, and `download` raise `NeedsReloginError` when there is nothing usable. Subscribe with `service.callbacks.on(SessionEvent.NEEDS_RELOGIN, handler)`. Handlers that raise are not swallowed.

Pacing state lives in the same SQLite file, so two processes on one volume share the bucket.

## Development

```bash
pip install -e ".[dev]"
pytest
```

Tests mock the browser. They do not download Chromium. CI is [`.github/workflows/ci.yml`](.github/workflows/ci.yml) on Python 3.11.

## License

MIT. Copyright (c) 2026 NabuxAI.
