# UAE Mattress Catalog — MCP server

Read-only MCP server (Streamable HTTP at `/mcp`) exposing an agent-readable catalog of mattresses sold in the UAE.
Data: public product information. Tools: `search_mattresses`, `get_product`, `compare_products`, `get_seller_policies`, `list_filters`, `search`, `fetch`.

Run locally: `pip install -r mcp-server/requirements.txt && python mcp-server/server.py` (port from `$PORT`, default 8765).
