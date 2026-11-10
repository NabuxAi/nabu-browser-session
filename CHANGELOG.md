# Changelog

All notable changes to `nabu_browser_session` are documented here.
The format is based on Keep a Changelog, and the package follows Semantic Versioning.

## 0.1.0 — 2026-11-10

Initial release of the shared unofficial browser-session package.

- Chromium via Playwright is the default engine. Lightpanda over CDP is optional and is used only for reads and downloads, with an automatic fallback to Chromium.
- Fernet-encrypted SQLite session vault keyed by owner, platform, and account.
- Session lifecycle with a `needs_relogin` flag, health checks, and callbacks. Authenticated actions raise when the session is missing or dead.
- Human login capture (QR or password typed by the account owner) with a framework-agnostic view the gateway dashboard can render.
- Per-account token bucket, minimum gap, and jitter, shared through the vault database.
- Generic media download helper.
- `PlatformAdapter` contract, `StubPlatformAdapter`, and the `{platform}_unofficial_{action}` MCP tool namespace.
