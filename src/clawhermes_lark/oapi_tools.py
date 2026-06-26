"""
Feishu Open API (OAPI) tools — Python wrappers for common Feishu API operations.

Aligns with larksuite/openclaw-lark src/tools/oapi/:
  - Sheets: read/write spreadsheet cells, manage spreadsheets
  - Calendar: list/create events, check free/busy
  - Drive: upload/download files, manage folders
  - Wiki: knowledge base operations
  - Docs: document content operations
  - IM/Chat: chat management, message helpers
  - Search: enterprise search
  - Common: user lookup/search

All tools accept a lark_oapi.Client instance and return dict results.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

logger = logging.getLogger("clawhermes.lark.oapi_tools")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fmt_result(data: Any) -> dict[str, Any]:
    """Format a successful API result."""
    return {"ok": True, "data": data}


def _fmt_error(msg: str, code: int = 0) -> dict[str, Any]:
    """Format an error result."""
    return {"ok": False, "error": msg, "code": code}


def _get_json(obj: Any) -> Any:
    """Convert API response data to JSON-serializable form."""
    if obj is None:
        return None
    if hasattr(obj, "__dict__"):
        return {k: _get_json(v) for k, v in obj.__dict__.items() if not k.startswith("_")}
    if isinstance(obj, list):
        return [_get_json(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _get_json(v) for k, v in obj.items()}
    return obj


# ---------------------------------------------------------------------------
# Sheets (Spreadsheets)
# ---------------------------------------------------------------------------


async def sheets_get_meta(
    client,
    spreadsheet_token: str,
) -> dict[str, Any]:
    """Get spreadsheet metadata (sheets, properties)."""
    try:
        import lark_oapi as lark
        from lark_oapi.api.sheets.v3 import GetSpreadsheetRequest

        req = GetSpreadsheetRequest.builder() \
            .spreadsheet_token(spreadsheet_token) \
            .build()
        resp = client.sheets.v3.spreadsheet.get(req)
        if resp.code != 0:
            return _fmt_error(f"code={resp.code} msg={resp.msg}", resp.code)
        return _fmt_result(_get_json(resp.data))
    except Exception as e:
        return _fmt_error(str(e))


async def sheets_read_values(
    client,
    spreadsheet_token: str,
    sheet_id: str,
    range_str: str,
) -> dict[str, Any]:
    """Read values from a spreadsheet range."""
    try:
        from lark_oapi.api.sheets.v3 import GetSpreadsheetSheetRangeRequest

        req = GetSpreadsheetSheetRangeRequest.builder() \
            .spreadsheet_token(spreadsheet_token) \
            .sheet_id(sheet_id) \
            .range(range_str) \
            .build()
        resp = client.sheets.v3.spreadsheet_sheet_range.get(req)
        if resp.code != 0:
            return _fmt_error(f"code={resp.code} msg={resp.msg}", resp.code)
        return _fmt_result(_get_json(resp.data))
    except Exception as e:
        return _fmt_error(str(e))


async def sheets_write_values(
    client,
    spreadsheet_token: str,
    sheet_id: str,
    range_str: str,
    values: list[list[Any]],
) -> dict[str, Any]:
    """Write values to a spreadsheet range."""
    try:
        import lark_oapi as lark
        from lark_oapi.api.sheets.v3 import (
            UpdateSpreadsheetSheetRangeRequest,
            UpdateSpreadsheetSheetRangeRequestBody,
        )

        body = UpdateSpreadsheetSheetRangeRequestBody.builder() \
            .values(values) \
            .build()
        req = UpdateSpreadsheetSheetRangeRequest.builder() \
            .spreadsheet_token(spreadsheet_token) \
            .sheet_id(sheet_id) \
            .range(range_str) \
            .request_body(body) \
            .build()
        resp = client.sheets.v3.spreadsheet_sheet_range.update(req)
        if resp.code != 0:
            return _fmt_error(f"code={resp.code} msg={resp.msg}", resp.code)
        return _fmt_result(_get_json(resp.data))
    except Exception as e:
        return _fmt_error(str(e))


async def sheets_list(
    client,
    page_size: int = 20,
    page_token: str = "",
) -> dict[str, Any]:
    """List spreadsheets the bot has access to."""
    try:
        from lark_oapi.api.drive.v1 import (
            ListFileRequest,
            ListFileRequestOrderBy,
        )

        req = ListFileRequest.builder() \
            .page_size(page_size) \
            .page_token(page_token) \
            .file_type("sheet") \
            .order_by(str(ListFileRequestOrderBy.EDITED_TIME)) \
            .build()
        resp = client.drive.v1.file.list(req)
        if resp.code != 0:
            return _fmt_error(f"code={resp.code} msg={resp.msg}", resp.code)
        return _fmt_result(_get_json(resp.data))
    except Exception as e:
        return _fmt_error(str(e))


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------


async def calendar_list(
    client,
    page_size: int = 20,
    page_token: str = "",
) -> dict[str, Any]:
    """List calendars."""
    try:
        from lark_oapi.api.calendar.v4 import ListCalendarRequest

        req = ListCalendarRequest.builder() \
            .page_size(page_size) \
            .page_token(page_token) \
            .build()
        resp = client.calendar.v4.calendar.list(req)
        if resp.code != 0:
            return _fmt_error(f"code={resp.code} msg={resp.msg}", resp.code)
        return _fmt_result(_get_json(resp.data))
    except Exception as e:
        return _fmt_error(str(e))


async def calendar_list_events(
    client,
    calendar_id: str,
    start_time: str = "",
    end_time: str = "",
    page_size: int = 50,
) -> dict[str, Any]:
    """List events in a calendar."""
    try:
        from lark_oapi.api.calendar.v4 import ListCalendarEventRequest

        builder = ListCalendarEventRequest.builder() \
            .calendar_id(calendar_id) \
            .page_size(page_size)
        if start_time:
            builder = builder.start_time(start_time)
        if end_time:
            builder = builder.end_time(end_time)
        req = builder.build()
        resp = client.calendar.v4.calendar_event.list(req)
        if resp.code != 0:
            return _fmt_error(f"code={resp.code} msg={resp.msg}", resp.code)
        return _fmt_result(_get_json(resp.data))
    except Exception as e:
        return _fmt_error(str(e))


async def calendar_create_event(
    client,
    calendar_id: str,
    summary: str,
    start_time: str,
    end_time: str,
    description: str = "",
    attendees: list[str] | None = None,
) -> dict[str, Any]:
    """Create a calendar event."""
    try:
        from lark_oapi.api.calendar.v4 import (
            CreateCalendarEventRequest,
            CreateCalendarEventRequestBody,
        )

        event_time = {
            "date_time": start_time,
            "timezone": "Asia/Shanghai",
        }
        body_builder = CreateCalendarEventRequestBody.builder() \
            .summary(summary) \
            .start_time(event_time) \
            .end_time({**event_time, "date_time": end_time})
        if description:
            body_builder = body_builder.description(description)
        if attendees:
            body_builder = body_builder.attendees(
                [{"type": "user", "user_id": a} for a in attendees]
            )

        req = CreateCalendarEventRequest.builder() \
            .calendar_id(calendar_id) \
            .request_body(body_builder.build()) \
            .build()
        resp = client.calendar.v4.calendar_event.create(req)
        if resp.code != 0:
            return _fmt_error(f"code={resp.code} msg={resp.msg}", resp.code)
        return _fmt_result(_get_json(resp.data))
    except Exception as e:
        return _fmt_error(str(e))


# ---------------------------------------------------------------------------
# Drive
# ---------------------------------------------------------------------------


async def drive_list_files(
    client,
    folder_token: str = "",
    page_size: int = 20,
    page_token: str = "",
) -> dict[str, Any]:
    """List files in Drive (root or specific folder)."""
    try:
        from lark_oapi.api.drive.v1 import (
            ListFileRequest,
            ListFileRequestOrderBy,
        )

        builder = ListFileRequest.builder() \
            .page_size(page_size) \
            .page_token(page_token) \
            .order_by(str(ListFileRequestOrderBy.EDITED_TIME))
        if folder_token:
            builder = builder.folder_token(folder_token)
        req = builder.build()
        resp = client.drive.v1.file.list(req)
        if resp.code != 0:
            return _fmt_error(f"code={resp.code} msg={resp.msg}", resp.code)
        return _fmt_result(_get_json(resp.data))
    except Exception as e:
        return _fmt_error(str(e))


async def drive_download_file(
    client,
    file_token: str,
) -> dict[str, Any]:
    """Download a file from Drive (returns metadata + content bytes)."""
    try:
        from lark_oapi.api.drive.v1 import (
            DownloadFileRequest,
            GetFileRequest,
        )

        # Get file metadata
        meta_req = GetFileRequest.builder() \
            .file_token(file_token) \
            .build()
        meta_resp = client.drive.v1.file.get(meta_req)

        if meta_resp.code != 0:
            return _fmt_error(f"metadata code={meta_resp.code} msg={meta_resp.msg}")

        meta = _get_json(meta_resp.data)

        # Download content
        dl_req = DownloadFileRequest.builder() \
            .file_token(file_token) \
            .build()
        dl_resp = client.drive.v1.file.download(dl_req)

        return _fmt_result({
            "metadata": meta,
            "content": dl_resp.file.read() if hasattr(dl_resp, "file") else None,
        })
    except Exception as e:
        return _fmt_error(str(e))


async def drive_search(
    client,
    query: str,
    page_size: int = 20,
) -> dict[str, Any]:
    """Search files in Drive."""
    try:
        from lark_oapi.api.drive.v1 import (
            SearchFileRequest,
            SearchFileRequestBody,
        )

        body = SearchFileRequestBody.builder() \
            .search_key(query) \
            .page_size(page_size) \
            .build()
        req = SearchFileRequest.builder() \
            .request_body(body) \
            .build()
        resp = client.drive.v1.file.search(req)
        if resp.code != 0:
            return _fmt_error(f"code={resp.code} msg={resp.msg}", resp.code)
        return _fmt_result(_get_json(resp.data))
    except Exception as e:
        return _fmt_error(str(e))


# ---------------------------------------------------------------------------
# Wiki (Knowledge Base)
# ---------------------------------------------------------------------------


async def wiki_list_spaces(
    client,
    page_size: int = 20,
    page_token: str = "",
) -> dict[str, Any]:
    """List wiki spaces."""
    try:
        from lark_oapi.api.wiki.v2 import ListSpaceRequest

        req = ListSpaceRequest.builder() \
            .page_size(page_size) \
            .page_token(page_token) \
            .build()
        resp = client.wiki.v2.space.list(req)
        if resp.code != 0:
            return _fmt_error(f"code={resp.code} msg={resp.msg}", resp.code)
        return _fmt_result(_get_json(resp.data))
    except Exception as e:
        return _fmt_error(str(e))


async def wiki_get_node(
    client,
    token: str,
) -> dict[str, Any]:
    """Get a wiki node (page) by token."""
    try:
        from lark_oapi.api.wiki.v2 import GetNodeRequest

        req = GetNodeRequest.builder() \
            .token(token) \
            .build()
        resp = client.wiki.v2.space_node.get(req)
        if resp.code != 0:
            return _fmt_error(f"code={resp.code} msg={resp.msg}", resp.code)
        return _fmt_result(_get_json(resp.data))
    except Exception as e:
        return _fmt_error(str(e))


async def wiki_list_nodes(
    client,
    space_id: str,
    parent_node_token: str = "",
    page_size: int = 20,
) -> dict[str, Any]:
    """List nodes (pages) in a wiki space."""
    try:
        from lark_oapi.api.wiki.v2 import (
            ListSpaceNodeRequest,
        )

        builder = ListSpaceNodeRequest.builder() \
            .space_id(space_id) \
            .page_size(page_size)
        if parent_node_token:
            builder = builder.parent_node_token(parent_node_token)
        req = builder.build()
        resp = client.wiki.v2.space_node.list(req)
        if resp.code != 0:
            return _fmt_error(f"code={resp.code} msg={resp.msg}", resp.code)
        return _fmt_result(_get_json(resp.data))
    except Exception as e:
        return _fmt_error(str(e))


# ---------------------------------------------------------------------------
# Docs
# ---------------------------------------------------------------------------


async def docs_get_content(
    client,
    document_id: str,
) -> dict[str, Any]:
    """Get document content blocks."""
    try:
        from lark_oapi.api.docx.v1 import (
            ListDocumentBlockRequest,
        )

        req = ListDocumentBlockRequest.builder() \
            .document_id(document_id) \
            .page_size(500) \
            .build()
        resp = client.docx.v1.document_block.list(req)
        if resp.code != 0:
            return _fmt_error(f"code={resp.code} msg={resp.msg}", resp.code)
        return _fmt_result(_get_json(resp.data))
    except Exception as e:
        return _fmt_error(str(e))


async def docs_get_meta(
    client,
    document_id: str,
) -> dict[str, Any]:
    """Get document metadata."""
    try:
        from lark_oapi.api.docx.v1 import GetDocumentRequest

        req = GetDocumentRequest.builder() \
            .document_id(document_id) \
            .build()
        resp = client.docx.v1.document.get(req)
        if resp.code != 0:
            return _fmt_error(f"code={resp.code} msg={resp.msg}", resp.code)
        return _fmt_result(_get_json(resp.data))
    except Exception as e:
        return _fmt_error(str(e))


# ---------------------------------------------------------------------------
# IM / Chat
# ---------------------------------------------------------------------------


async def im_get_chat_info(
    client,
    chat_id: str,
) -> dict[str, Any]:
    """Get chat info (name, member count, etc.)."""
    try:
        from lark_oapi.api.im.v1 import GetChatRequest

        req = GetChatRequest.builder() \
            .chat_id(chat_id) \
            .build()
        resp = client.im.v1.chat.get(req)
        if resp.code != 0:
            return _fmt_error(f"code={resp.code} msg={resp.msg}", resp.code)
        return _fmt_result(_get_json(resp.data))
    except Exception as e:
        return _fmt_error(str(e))


async def im_list_chat_members(
    client,
    chat_id: str,
    page_size: int = 100,
) -> dict[str, Any]:
    """List members of a chat."""
    try:
        from lark_oapi.api.im.v1 import (
            ListChatMembersRequest,
        )

        req = ListChatMembersRequest.builder() \
            .chat_id(chat_id) \
            .page_size(page_size) \
            .build()
        resp = client.im.v1.chat_members.list(req)
        if resp.code != 0:
            return _fmt_error(f"code={resp.code} msg={resp.msg}", resp.code)
        return _fmt_result(_get_json(resp.data))
    except Exception as e:
        return _fmt_error(str(e))


# ---------------------------------------------------------------------------
# Common — User Operations
# ---------------------------------------------------------------------------


async def common_get_user(
    client,
    user_id: str,
    user_id_type: str = "open_id",
) -> dict[str, Any]:
    """Get user information."""
    try:
        from lark_oapi.api.contact.v3 import GetUserRequest

        req = GetUserRequest.builder() \
            .user_id(user_id) \
            .user_id_type(user_id_type) \
            .build()
        resp = client.contact.v3.user.get(req)
        if resp.code != 0:
            return _fmt_error(f"code={resp.code} msg={resp.msg}", resp.code)
        return _fmt_result(_get_json(resp.data))
    except Exception as e:
        return _fmt_error(str(e))


async def common_search_users(
    client,
    query: str,
    page_size: int = 20,
) -> dict[str, Any]:
    """Search users in the organization."""
    try:
        from lark_oapi.api.contact.v3 import (
            SearchUserRequest,
            SearchUserRequestBody,
        )

        body = SearchUserRequestBody.builder() \
            .query(query) \
            .page_size(page_size) \
            .build()
        req = SearchUserRequest.builder() \
            .request_body(body) \
            .build()
        resp = client.contact.v3.user.search(req)
        if resp.code != 0:
            return _fmt_error(f"code={resp.code} msg={resp.msg}", resp.code)
        return _fmt_result(_get_json(resp.data))
    except Exception as e:
        return _fmt_error(str(e))


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


async def search_enterprise(
    client,
    query: str,
    search_type: str = "all",
    page_size: int = 20,
) -> dict[str, Any]:
    """Enterprise search across docs, sheets, wiki, etc."""
    try:
        from lark_oapi.api.search.v2 import (
            SearchRequest,
            SearchRequestBody,
        )

        body = SearchRequestBody.builder() \
            .query(query) \
            .page_size(page_size) \
            .build()
        req = SearchRequest.builder() \
            .search_type(search_type) \
            .request_body(body) \
            .build()
        resp = client.search.v2.search.create(req)
        if resp.code != 0:
            return _fmt_error(f"code={resp.code} msg={resp.msg}", resp.code)
        return _fmt_result(_get_json(resp.data))
    except Exception as e:
        return _fmt_error(str(e))


# ---------------------------------------------------------------------------
# Tool Registry
# ---------------------------------------------------------------------------

# Map of tool names to handler functions
# Each entry: (function, description, param_schema)
OAPI_TOOL_REGISTRY: dict[str, dict[str, Any]] = {
    # Sheets
    "sheets_get_meta": {
        "fn": sheets_get_meta,
        "desc": "Get spreadsheet metadata (sheets list, properties)",
        "params": {"spreadsheet_token": "str"},
    },
    "sheets_read_values": {
        "fn": sheets_read_values,
        "desc": "Read values from a spreadsheet range (e.g. A1:C10)",
        "params": {"spreadsheet_token": "str", "sheet_id": "str", "range": "str"},
    },
    "sheets_write_values": {
        "fn": sheets_write_values,
        "desc": "Write values to a spreadsheet range",
        "params": {"spreadsheet_token": "str", "sheet_id": "str", "range": "str", "values": "list"},
    },
    "sheets_list": {
        "fn": sheets_list,
        "desc": "List spreadsheets the bot has access to",
        "params": {},
    },
    # Calendar
    "calendar_list": {
        "fn": calendar_list,
        "desc": "List available calendars",
        "params": {},
    },
    "calendar_list_events": {
        "fn": calendar_list_events,
        "desc": "List events in a calendar (optionally filtered by time range)",
        "params": {"calendar_id": "str", "start_time": "str?", "end_time": "str?"},
    },
    "calendar_create_event": {
        "fn": calendar_create_event,
        "desc": "Create a calendar event with optional attendees",
        "params": {"calendar_id": "str", "summary": "str", "start_time": "str", "end_time": "str"},
    },
    # Drive
    "drive_list_files": {
        "fn": drive_list_files,
        "desc": "List files in Drive (root or folder)",
        "params": {"folder_token": "str?"},
    },
    "drive_download_file": {
        "fn": drive_download_file,
        "desc": "Download a file from Drive by file token",
        "params": {"file_token": "str"},
    },
    "drive_search": {
        "fn": drive_search,
        "desc": "Search files in Drive by keyword",
        "params": {"query": "str"},
    },
    # Wiki
    "wiki_list_spaces": {
        "fn": wiki_list_spaces,
        "desc": "List wiki knowledge base spaces",
        "params": {},
    },
    "wiki_get_node": {
        "fn": wiki_get_node,
        "desc": "Get a wiki page by token",
        "params": {"token": "str"},
    },
    "wiki_list_nodes": {
        "fn": wiki_list_nodes,
        "desc": "List pages in a wiki space",
        "params": {"space_id": "str", "parent_node_token": "str?"},
    },
    # Docs
    "docs_get_content": {
        "fn": docs_get_content,
        "desc": "Get document content blocks",
        "params": {"document_id": "str"},
    },
    "docs_get_meta": {
        "fn": docs_get_meta,
        "desc": "Get document metadata (title, owner, etc.)",
        "params": {"document_id": "str"},
    },
    # IM / Chat
    "im_get_chat_info": {
        "fn": im_get_chat_info,
        "desc": "Get chat group info (name, member count)",
        "params": {"chat_id": "str"},
    },
    "im_list_chat_members": {
        "fn": im_list_chat_members,
        "desc": "List members of a chat group",
        "params": {"chat_id": "str"},
    },
    # Common
    "common_get_user": {
        "fn": common_get_user,
        "desc": "Get user profile information",
        "params": {"user_id": "str", "user_id_type": "str?"},
    },
    "common_search_users": {
        "fn": common_search_users,
        "desc": "Search users in the organization by name/email",
        "params": {"query": "str"},
    },
    # Search
    "search_enterprise": {
        "fn": search_enterprise,
        "desc": "Enterprise search across docs, sheets, wiki, etc.",
        "params": {"query": "str", "search_type": "str?"},
    },
}


def get_oapi_tool(name: str) -> Callable | None:
    """Get an OAPI tool function by name."""
    entry = OAPI_TOOL_REGISTRY.get(name)
    return entry["fn"] if entry else None


def list_oapi_tools() -> list[dict[str, Any]]:
    """List all registered OAPI tools with descriptions."""
    return [
        {"name": name, "desc": info["desc"], "params": info["params"]}
        for name, info in OAPI_TOOL_REGISTRY.items()
    ]


async def invoke_oapi_tool(
    client,
    tool_name: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Invoke an OAPI tool by name with the given parameters.

    Args:
        client: lark_oapi.Client instance
        tool_name: Name of the tool (e.g. "sheets_read_values")
        params: Dict of parameter values

    Returns:
        Result dict with {"ok": True, "data": ...} or {"ok": False, "error": ...}
    """
    entry = OAPI_TOOL_REGISTRY.get(tool_name)
    if not entry:
        return _fmt_error(f"Unknown OAPI tool: {tool_name}")

    fn = entry["fn"]
    kwargs = params or {}

    try:
        result = await fn(client, **kwargs)
        return result
    except Exception as e:
        logger.exception("OAPI tool error: %s", tool_name)
        return _fmt_error(str(e))
