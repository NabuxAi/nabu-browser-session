"""Print the unofficial-session configuration without revealing secrets.

Usage: ``python -m nabu_browser_session``
"""

from __future__ import annotations

import os

from nabu_browser_session import __version__
from nabu_browser_session.config import FERNET_KEY_ALIAS, FERNET_KEY_ENV, Settings
from nabu_browser_session.engine import Capability, EngineSelector


def main() -> None:
    settings = Settings.from_env()
    key_set = bool(os.environ.get(FERNET_KEY_ENV) or os.environ.get(FERNET_KEY_ALIAS))
    selector = EngineSelector(settings)
    print(f"nabu_browser_session {__version__}")
    print("mode: UNOFFICIAL browser session")
    print(f"browser_engine: {settings.browser_engine}")
    print(f"lightpanda_cdp_url_set: {bool(settings.lightpanda_cdp_url)}")
    print(f"headless: {settings.headless}")
    print(f"session_db_path: {settings.session_db_path}")
    print(f"fernet_key_set: {key_set}")
    print(f"rate_enabled: {settings.rate_enabled}")
    print(
        "rate: "
        f"capacity={settings.rate_capacity} "
        f"refill_per_minute={settings.rate_refill_per_minute} "
        f"min_interval_seconds={settings.rate_min_interval_seconds} "
        f"jitter_seconds={settings.rate_jitter_seconds}"
    )
    for capability in Capability:
        choice = selector.choose(capability)
        print(
            f"capability {capability.value}: "
            f"engine={choice.kind.value} fell_back={str(choice.fell_back).lower()}"
        )


if __name__ == "__main__":
    main()
