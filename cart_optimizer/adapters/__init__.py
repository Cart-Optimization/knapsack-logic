"""Adapters that normalize external menu payloads into cart_optimizer Menus.

``swiggy.py`` (not yet written) will map a real Swiggy MCP Food response to
``models.Menu``. It must be shaped against a captured live payload — the
server's tool schemas are not published. To capture one:

1. Connect the remote server to a Claude Code session:
       claude mcp add --transport http swiggy-food https://mcp.swiggy.com/food
   then authenticate via the OAuth browser flow (/mcp).
2. List the server's tools, fetch one restaurant's menu, and save the raw
   JSON under tests/fixtures/.
3. Write the adapter + tests against that fixture.

Safety: the Food server is COD-only and orders cannot be cancelled — never
call any order-placement tool during testing; menu reads are safe.
"""
