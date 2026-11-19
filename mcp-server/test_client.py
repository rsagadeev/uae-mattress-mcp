"""Smoke test: python test_client.py [URL]"""
import asyncio, json, sys
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8765/mcp"
FULL = "--full" in sys.argv

CALLS = [
    ("search_mattresses", {"firmness_min": 5, "firmness_max": 7, "size": "160x200", "price_max": 2000,
                           "emirate": "Abu Dhabi", "payment_method": "tabby", "sort": "price_asc", "limit": 5}),
    ("search_mattresses", {"audience": "kids", "sort": "price_asc", "limit": 5, "one_per_product": True}),
    ("search_mattresses", {"size_class": "king", "sort": "firmness", "limit": 3}),
    ("search_mattresses", {"features": ["cooling", "orthopedic"], "size": "180x200", "limit": 3, "sort": "price_asc"}),
    ("list_filters", {}),
    ("get_product", {"product_id": "sleep-professor:arizona-duo", "size": "160x200"}),
    ("compare_products", {"product_ids": ["sleep-professor:arizona-duo", "askona:askona-ortho-hard"], "size": "160x200"}),
    ("get_seller_policies", {"seller_id": "askona-gulf"}),
    ("get_seller_policies", {"seller_id": "the-mattress-store"}),
    ("get_seller_policies", {}),
    ("search_mattresses", {"seller": "the-mattress-store", "size": "160x200", "price_max": 3000,
                           "sort": "price_asc", "limit": 5, "one_per_product": True}),
    ("search_mattresses", {"seller": "askona-gulf", "size": "160x200", "price_max": 2000,
                           "sort": "price_asc", "limit": 3, "one_per_product": True}),
    ("search_mattresses", {"query": "Bodiform Gel Tight Top", "size": "160x200", "limit": 5}),
    ("compare_products", {"product_ids": ["englander:bodiform-gel-tight-top", "sleep-professor:arizona-duo"],
                          "size": "160x200"}),
    ("search", {"query": "medium firm mattress 160x200 under 2000 AED delivered to Abu Dhabi, pay with Tabby"}),
]


def short(x, n=1500):
    s = json.dumps(x, ensure_ascii=False)
    return s if FULL else s[:n] + ("…" if len(s) > n else "")


async def main():
    async with streamable_http_client(URL) as streams:
        read, write = streams[0], streams[1]
        async with ClientSession(read, write) as s:
            init = await s.initialize()
            print("SERVER:", init.server_info.name, init.server_info.version, "| instructions:", (init.instructions or "")[:80])
            tools = await s.list_tools()
            print("TOOLS:", [t.name for t in tools.tools])
            only = [a for a in sys.argv[2:] if not a.startswith("--")]
            fetch_id = None
            for name, args in CALLS:
                if only and name not in only:
                    continue
                r = await s.call_tool(name, args)
                sc = r.structured_content
                print(f"\n=== {name} {args}  is_error={r.is_error}")
                if name == "search_mattresses" and sc:
                    print("total:", sc["total_matching_offers"], "notes:", sc.get("notes"))
                    for x in sc["results"]:
                        print(f"  [{x.get('seller')}] {x['name']} {x['size']} firm={x['firmness'].get('scale_1_10') or [y['scale_1_10'] for y in x['firmness'].get('sides', [])]} "
                              f"{x['regular_price_aed']}->{x['current_price_aed']} AED until {x['sale_ends']} | {x['availability']} | {x['delivery']} | {x['url']}")
                elif name == "search" and sc:
                    for x in sc["results"]:
                        print("  ", x)
                    fetch_id = sc["results"][0]["id"] if sc["results"] else None
                else:
                    print(short(sc if sc is not None else [c.text for c in r.content]))
            if fetch_id and (not only or "fetch" in only or "search" in only):
                r = await s.call_tool("fetch", {"id": fetch_id})
                print("\n=== fetch", fetch_id, short(r.structured_content, 600))

asyncio.run(main())
