"""How a Nabux gateway wires unofficial browser login beside its official API.

Copy this into the gateway. FastMCP stays a gateway dependency; this package
does not import it. Swap StubPlatformAdapter for the platform adapter.
"""

from fastmcp import FastMCP

from nabu_browser_session import (
    BrowserSessionService,
    StubPlatformAdapter,
    unofficial_tool_name,
)

mcp = FastMCP("example-gateway")
sessions = BrowserSessionService.from_env()
adapter = StubPlatformAdapter()


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
