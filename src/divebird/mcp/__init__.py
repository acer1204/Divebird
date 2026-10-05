"""MCP（Model Context Protocol）伺服器：讓支援 MCP 的 AI 工具透過 http://127.0.0.1:<埠>/mcp 使用 Divebird。

- protocol.py：Streamable HTTP 上的 JSON-RPC，同時支援 2026-07-28（每個請求自帶版本）與
  2025-03-26 ～ 2025-11-25（initialize 握手）兩個世代，一律只回 JSON
- tools.py：提供給 AI 的工具（新增下載、查詢進度、暫停／繼續、移除…）與權限檢查
- token.py：存取權杖（存在資料夾中的 mcp-token）
"""
