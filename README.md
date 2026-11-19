# UAE Mattress Catalog — MCP server

Read-only MCP server (Streamable HTTP at `/mcp`) exposing an agent-readable catalog of mattresses sold in the UAE.
Data: public product information from the sellers' websites. Tools: `search_mattresses`, `get_product`, `compare_products`, `get_seller_policies`, `get_reviews`, `get_seller_reviews`, `list_filters`, `search`, `fetch`.

Run locally: `pip install -r mcp-server/requirements.txt && python mcp-server/server.py` (port from `$PORT`, default 8765). Public endpoint: `https://uae-mattress-mcp.onrender.com/mcp`.

Catalog version 0.4.1 (Maps showroom ratings refreshed 2026-11-19). Base: two sellers — Askona Gulf (brand-direct: Askona, Sleep Professor, Grether & Wells) and The Mattress Store (authorized retailer: Englander, Vispring, Colunex). Each product has semantic fit attributes (weight range, sleep positions, motion isolation, couples, cooling, orthopedic, edge support, value-for-money) with provenance (`brand_stated` / `derived_rule` / `from_reviews`) — rules in `docs/fit_rules_ru.md`. Search results include rating + review count and compact fit. Filters: `sleep_position`, `sleeper_weight_kg`, `min_rating`, `couple`, `motion_isolation`. `get_product` returns the fit block, a review summary, up to 5 reviews with original customer photo URLs, and seller store ratings. New tools: `get_reviews`, `get_seller_reviews`. Products with zero reviews are labelled explicitly. Onboarding gaps for The Mattress Store: `docs/tms_onboarding_gaps_ru.md`.
