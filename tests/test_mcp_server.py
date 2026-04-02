from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

MCP_AVAILABLE = importlib.util.find_spec("mcp") is not None

if MCP_AVAILABLE:
    from mcp import ClientSession, StdioServerParameters, types
    from mcp.client.stdio import stdio_client


@unittest.skipUnless(MCP_AVAILABLE, "mcp package is not installed")
class McpServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            part for part in [env.get("PYTHONPATH"), str(SRC)] if part
        )
        self.server_params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "labbook", "mcp"],
            env=env,
        )

    async def test_list_tools(self) -> None:
        async with stdio_client(self.server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()

        tool_names = {tool.name for tool in tools.tools}
        self.assertIn("notion_status", tool_names)
        self.assertIn("notion_bind_resources", tool_names)

    async def test_setup_guide_tool(self) -> None:
        async with stdio_client(self.server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("notion_setup_guide", {})

        self.assertFalse(result.isError)
        self.assertTrue(result.content)
        self.assertIsInstance(result.content[0], types.TextContent)
        self.assertIn("Agent Labbook Public Integration Setup", result.content[0].text)
        self.assertEqual(result.structuredContent["result"].splitlines()[0], "# Agent Labbook Public Integration Setup")


if __name__ == "__main__":
    unittest.main()
