"""In-memory browser stand-ins. Tests never launch Chromium or Lightpanda."""

from __future__ import annotations

from nabu_browser_session import Capability, EngineKind, EngineUnavailable


class FakePage:
    def __init__(self, url: str = "about:blank") -> None:
        self.url = url
        self.context: FakeContext | None = None
        self.clicks: list[str] = []
        self.fills: list[tuple[str, str]] = []
        self.pressed: list[tuple[str, str]] = []
        self.title_text = "Login"

    async def goto(self, url: str, **kwargs: object) -> None:
        self.url = url

    async def screenshot(self, **kwargs: object) -> bytes:
        return b"\x89PNG\r\nfake"

    async def title(self) -> str:
        return self.title_text

    async def content(self) -> str:
        return "<html></html>"

    async def click(self, selector: str, **kwargs: object) -> None:
        self.clicks.append(selector)

    async def fill(self, selector: str, value: str, **kwargs: object) -> None:
        self.fills.append((selector, value))

    async def type(self, selector: str, value: str, **kwargs: object) -> None:
        self.fills.append((selector, value))

    async def press(self, selector: str, key: str, **kwargs: object) -> None:
        self.pressed.append((selector, key))


class FakeContext:
    def __init__(self, page: FakePage, state: dict | None = None) -> None:
        self.page = page
        self.state = state or {"cookies": [], "origins": []}
        page.context = self
        self.closed = False

    async def new_page(self) -> FakePage:
        return self.page

    async def storage_state(self) -> dict:
        return self.state

    async def close(self) -> None:
        self.closed = True


class FakeEngine:
    def __init__(self, kind: EngineKind, *, fail_launch: bool = False, fail_context: bool = False) -> None:
        self.kind = kind
        self.fail_launch = fail_launch
        self.fail_context = fail_context
        self.launched = False
        self.closed = False
        self.page = FakePage()
        self.context = FakeContext(self.page)

    def supports(self, capability: Capability) -> bool:
        if self.kind is EngineKind.LIGHTPANDA:
            return capability in {Capability.READ, Capability.DOWNLOAD}
        return True

    async def launch(self) -> None:
        if self.fail_launch:
            raise EngineUnavailable("lightpanda down")
        self.launched = True

    async def close(self) -> None:
        self.closed = True
        self.launched = False

    async def new_context(self, *, storage_state: dict | None = None) -> FakeContext:
        if self.fail_context:
            raise EngineUnavailable("lightpanda context")
        if storage_state is not None:
            self.context.state = storage_state
        return self.context


class EngineFactory:
    def __init__(self, *, fail_launch: set[EngineKind] | None = None, fail_context: set[EngineKind] | None = None) -> None:
        self.fail_launch = fail_launch or set()
        self.fail_context = fail_context or set()
        self.engines: list[FakeEngine] = []
        self.kinds: list[EngineKind] = []

    def __call__(self, kind: EngineKind, settings: object) -> FakeEngine:
        engine = FakeEngine(
            kind,
            fail_launch=kind in self.fail_launch,
            fail_context=kind in self.fail_context,
        )
        self.engines.append(engine)
        self.kinds.append(kind)
        return engine


class ManualClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t
