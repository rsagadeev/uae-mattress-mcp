# UAE Mattress Catalog — MCP server

Read-only MCP server (Streamable HTTP at `/mcp`) exposing an agent-readable catalog of mattresses sold in the UAE.
Data: public product information from the sellers' websites. Tools: `search_mattresses`, `get_product`, `compare_products`, `get_seller_policies`, `list_filters`, `search`, `fetch`.

Run locally: `pip install -r mcp-server/requirements.txt && python mcp-server/server.py` (port from `$PORT`, default 8765). Public endpoint: `https://uae-mattress-mcp.onrender.com/mcp`.

Catalog version 0.3.0: two sellers — Askona Gulf (brand-direct: Askona, Sleep Professor, Grether & Wells) and The Mattress Store (authorized retailer: Englander, Vispring, Colunex). `search_mattresses` accepts a `seller` filter; results always include `seller` / `seller_id`. `get_seller_policies` works for both sellers (omit `seller_id` for all). `compare_products` lists each seller's offer when a size is sold by more than one shop. Onboarding gaps for The Mattress Store: `docs/tms_onboarding_gaps_ru.md`.
