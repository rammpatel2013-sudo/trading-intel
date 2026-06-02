"""MCP server — exposes trading-intel's read layer to Claude Desktop.

The MCP server is a thin adapter: tools wrap the existing dashboard data
layer and the AM-summary synthesis. It never owns business logic, never
writes to the DB, and never emits signals (FlashAlpha rule 4).

Composition root: ``trading_intel.mcp.server``. Tool functions live in
``trading_intel.mcp.tools`` so they can be unit-tested in isolation from
FastMCP decoration.
"""
