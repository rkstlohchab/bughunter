"""Local stdio MCP server. It exposes no approval, scope-edit or shell tool."""
from __future__ import annotations

import asyncio
import json
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, ImageContent, Tool

from .model_tools import ModelTools, TOOL_SPECS, STUDY_TOOL_NAMES


async def serve(app, run_id: str):
    run = app.store.get("run", run_id) or {}
    study = run.get("kind") == "study"
    specs = [s for s in TOOL_SPECS if s["name"] in STUDY_TOOL_NAMES] if study else TOOL_SPECS
    await serve_capability(ModelTools(app,run_id,study=study),specs,"bughunter")


async def serve_capability(capability,specs,name):
    server = Server(name)

    @server.list_tools()
    async def list_tools():
        return [Tool(name=s["name"],description=s["description"],inputSchema=s["parameters"]) for s in specs]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        try:
            result = await asyncio.to_thread(capability.call,name,arguments or {})
            if name == "get_source_frame" and isinstance(result,dict) and "image_base64" in result:
                return [TextContent(type="text",text=result["locator"]),ImageContent(type="image",data=result["image_base64"],mimeType=result["media_type"])]
            return [TextContent(type="text",text=json.dumps(result,ensure_ascii=False,default=str))]
        except Exception as error:
            return [TextContent(type="text",text=json.dumps({"error":str(error)}))]

    async with stdio_server() as (reader, writer):
        await server.run(reader,writer,server.create_initialization_options())
