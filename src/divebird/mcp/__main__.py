"""python -m divebird.mcp：MCP 的 stdio 橋接（也是打包版 divebird-mcp 的進入點，所以用絕對匯入）。"""
import sys

from divebird.mcp.stdio import main

if __name__ == "__main__":
    sys.exit(main())
