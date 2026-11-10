from __future__ import annotations

import pytest

from nabu_browser_session import (
    Capability,
    ChromiumEngine,
    ConfigurationError,
    EngineKind,
    EngineSelector,
    EngineUnavailable,
    LightpandaEngine,
    Settings,
    open_engine,
    run_with_engine,
)
from tests.fakes import EngineFactory


def _settings(tmp_path, **env: str) -> Settings:
    base = {
        "SESSION_DIR": str(tmp_path / "sessions"),
        "NABU_RATE_MIN_INTERVAL_SECONDS": "0",
        "NABU_RATE_JITTER_SECONDS": "0",
    }
    base.update(env)
    return Settings.from_env(base)


@pytest.mark.parametrize(
    ("engine", "cdp", "capability", "kind", "fell_back"),
    [
        ("", "", Capability.READ, EngineKind.CHROMIUM, False),
        ("chromium", "", Capability.LOGIN, EngineKind.CHROMIUM, False),
        ("lightpanda", "ws://127.0.0.1:9222", Capability.READ, EngineKind.LIGHTPANDA, False),
        ("lightpanda", "ws://127.0.0.1:9222", Capability.DOWNLOAD, EngineKind.LIGHTPANDA, False),
        ("lightpanda", "ws://127.0.0.1:9222", Capability.LOGIN, EngineKind.CHROMIUM, True),
        ("lightpanda", "ws://127.0.0.1:9222", Capability.UPLOAD, EngineKind.CHROMIUM, True),
        ("lightpanda", "ws://127.0.0.1:9222", Capability.SEND, EngineKind.CHROMIUM, True),
        ("lightpanda", "", Capability.READ, EngineKind.CHROMIUM, True),
        ("LIGHTPANDA", "ws://127.0.0.1:9222", Capability.READ, EngineKind.LIGHTPANDA, False),
    ],
)
def test_engine_selection(tmp_path, engine, cdp, capability, kind, fell_back) -> None:
    settings = _settings(tmp_path, BROWSER_ENGINE=engine, LIGHTPANDA_CDP_URL=cdp)
    choice = EngineSelector(settings).choose(capability)
    assert choice.kind is kind
    assert choice.fell_back is fell_back
    assert choice.capability is capability


def test_invalid_engine_name(tmp_path) -> None:
    with pytest.raises(ConfigurationError):
        _settings(tmp_path, BROWSER_ENGINE="webkit")


def test_declared_support_does_not_launch(tmp_path) -> None:
    settings = _settings(tmp_path, BROWSER_ENGINE="lightpanda", LIGHTPANDA_CDP_URL="ws://127.0.0.1:9222")
    assert ChromiumEngine(settings).supports(Capability.LOGIN) is True
    lightpanda = LightpandaEngine(settings)
    assert lightpanda.supports(Capability.READ) is True
    assert lightpanda.supports(Capability.LOGIN) is False
    assert lightpanda.supports(Capability.UPLOAD) is False
    assert lightpanda.launched is False


async def test_lightpanda_without_url_fails_before_use(tmp_path) -> None:
    settings = _settings(tmp_path, BROWSER_ENGINE="chromium")
    engine = LightpandaEngine(settings)
    with pytest.raises(EngineUnavailable, match="LIGHTPANDA_CDP_URL"):
        await engine.launch()


async def test_launch_failure_falls_back_to_chromium(tmp_path) -> None:
    settings = _settings(tmp_path, BROWSER_ENGINE="lightpanda", LIGHTPANDA_CDP_URL="ws://127.0.0.1:9222")
    factory = EngineFactory(fail_launch={EngineKind.LIGHTPANDA})

    async def operation(engine, choice):
        assert engine.launched is True
        return choice

    choice = await run_with_engine(Capability.READ, operation, settings, builder=factory)
    assert choice.kind is EngineKind.CHROMIUM
    assert choice.fell_back is True
    assert factory.kinds == [EngineKind.LIGHTPANDA, EngineKind.CHROMIUM]
    assert all(engine.closed for engine in factory.engines)


async def test_mid_flow_lightpanda_failure_retries_on_chromium(tmp_path) -> None:
    settings = _settings(tmp_path, BROWSER_ENGINE="lightpanda", LIGHTPANDA_CDP_URL="ws://127.0.0.1:9222")
    factory = EngineFactory(fail_context={EngineKind.LIGHTPANDA})
    seen: list[EngineKind] = []

    async def operation(engine, choice):
        seen.append(engine.kind)
        await engine.new_context()
        return choice

    choice = await run_with_engine(Capability.DOWNLOAD, operation, settings, builder=factory)
    assert seen == [EngineKind.LIGHTPANDA, EngineKind.CHROMIUM]
    assert choice.kind is EngineKind.CHROMIUM
    assert choice.fell_back is True


async def test_generic_errors_are_not_retried(tmp_path) -> None:
    settings = _settings(tmp_path, BROWSER_ENGINE="lightpanda", LIGHTPANDA_CDP_URL="ws://127.0.0.1:9222")
    factory = EngineFactory()
    calls: list[EngineKind] = []

    async def operation(engine, choice):
        calls.append(engine.kind)
        raise RuntimeError("send failed")

    with pytest.raises(RuntimeError, match="send failed"):
        await run_with_engine(Capability.READ, operation, settings, builder=factory)
    assert calls == [EngineKind.LIGHTPANDA]


async def test_open_engine_closes(tmp_path) -> None:
    settings = _settings(tmp_path)
    factory = EngineFactory()
    async with open_engine(Capability.LOGIN, settings, builder=factory) as (engine, choice):
        assert choice.kind is EngineKind.CHROMIUM
        assert engine.launched is True
    assert engine.closed is True
