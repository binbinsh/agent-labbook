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
        self.assertIn("notion_finalize_pending_auth", tool_names)

        status_tool = next(tool for tool in tools.tools if tool.name == "notion_status")
        self.assertIsNotNone(status_tool.annotations)
        self.assertTrue(status_tool.annotations.readOnlyHint)
        self.assertIsNotNone(status_tool.outputSchema)

        guide_tool = next(tool for tool in tools.tools if tool.name == "notion_setup_guide")
        self.assertIsNotNone(guide_tool.outputSchema)

    async def test_setup_guide_tool(self) -> None:
        async with stdio_client(self.server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("notion_setup_guide", {})

        self.assertFalse(result.isError)
        self.assertTrue(result.content)
        self.assertIsInstance(result.content[0], types.TextContent)
        self.assertIn("Agent Labbook Public Integration Setup", result.content[0].text)
        self.assertEqual(
            result.structuredContent["guide_markdown"].splitlines()[0],
            "# Agent Labbook Public Integration Setup",
        )
        self.assertEqual(
            result.structuredContent["resource_uri"],
            "labbook://agent-labbook/setup-guide",
        )

    async def test_status_tool_returns_structured_output(self) -> None:
        async with stdio_client(self.server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("notion_status", {})

        self.assertFalse(result.isError)
        self.assertEqual(result.structuredContent["integration"], "agent-labbook")
        self.assertIn("recommended_action", result.structuredContent)
        self.assertIn("scope_choice_hint", result.structuredContent)
        self.assertIn("connect_decision", result.structuredContent)
        self.assertTrue(result.structuredContent["connect_decision"]["requires_user_choice"])
        self.assertEqual(len(result.structuredContent["connect_decision"]["questions"]), 2)
        self.assertIn("blocking_hint", result.structuredContent["connect_decision"])
        self.assertIn("manual_prompt_markdown", result.structuredContent["connect_decision"])
        self.assertIn("route_templates", result.structuredContent["connect_decision"])
        self.assertTrue(result.content)

    async def test_resources_expose_status_and_setup_guide(self) -> None:
        async with stdio_client(self.server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                resources = await session.list_resources()
                status_result = await session.read_resource("labbook://agent-labbook/project/status")
                guide_result = await session.read_resource("labbook://agent-labbook/setup-guide")

        resource_uris = {str(resource.uri) for resource in resources.resources}
        self.assertIn("labbook://agent-labbook/project/status", resource_uris)
        self.assertIn("labbook://agent-labbook/setup-guide", resource_uris)
        self.assertTrue(status_result.contents)
        self.assertIn('"integration": "agent-labbook"', status_result.contents[0].text)
        self.assertTrue(guide_result.contents)
        self.assertIn("Agent Labbook Public Integration Setup", guide_result.contents[0].text)

    async def test_prompts_expose_guided_workflows(self) -> None:
        async with stdio_client(self.server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                prompts = await session.list_prompts()
                prompt = await session.get_prompt("notion_connect_project")

        prompt_names = {item.name for item in prompts.prompts}
        self.assertIn("notion_connect_project", prompt_names)
        self.assertIn("notion_use_bound_resources", prompt_names)
        self.assertTrue(prompt.messages)
        self.assertIsInstance(prompt.messages[0].content, types.TextContent)
        self.assertIn("notion_finalize_pending_auth", prompt.messages[0].content.text)
        self.assertIn("saved_credentials_error", prompt.messages[0].content.text)
        self.assertIn("connect_decision.questions", prompt.messages[0].content.text)
        self.assertIn("Do not choose scope_mode or browser_mode on the user's behalf", prompt.messages[0].content.text)
        self.assertIn("exactly once on its own line", prompt.messages[0].content.text)
        self.assertIn("manual_prompt_markdown", prompt.messages[0].content.text)


if __name__ == "__main__":
    unittest.main()
