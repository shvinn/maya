"""How every Maya tool reports a refusal.

A business-rule failure -- sold out, not refundable, too late to cancel --
is returned as an MCP tool error: ``isError`` is true, so a client that only
checks that flag can't mistake a refused booking for a successful one. The
details stay structured: ``structuredContent`` (and the text content, as
JSON) is ``{"error": {"code", "message", "hint"}}``, where ``code`` is
stable and machine-readable and ``hint`` says honestly whether retrying can
help.
"""

from __future__ import annotations

import json

from mcp_types import CallToolResult, TextContent


def tool_error(code: str, message: str, hint: str | None = None) -> CallToolResult:
    error: dict = {"code": code, "message": message}
    if hint:
        error["hint"] = hint
    payload = {"error": error}
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload))],
        structured_content=payload,
        is_error=True,
    )
