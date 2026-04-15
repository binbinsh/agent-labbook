from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from urllib import parse


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

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
        self.assertIn("notion_prepare_internal_integration", tool_names)
        self.assertIn("notion_configure_internal_integration", tool_names)
        self.assertIn("notion_search_resources", tool_names)
        self.assertIn("notion_discover_children", tool_names)
        self.assertIn("notion_bind_resource_urls", tool_names)
        self.assertIn("notion_bind_resources", tool_names)
        self.assertIn("notion_open_binding_browser", tool_names)

        status_tool = next(tool for tool in tools.tools if tool.name == "notion_status")
        self.assertIsNotNone(status_tool.annotations)
        self.assertTrue(status_tool.annotations.readOnlyHint)
        self.assertIsNotNone(status_tool.outputSchema)

        guide_tool = next(
            tool for tool in tools.tools if tool.name == "notion_setup_guide"
        )
        self.assertIsNotNone(guide_tool.outputSchema)

    async def test_setup_guide_tool(self) -> None:
        async with stdio_client(self.server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("notion_setup_guide", {})

        self.assertFalse(result.isError)
        self.assertTrue(result.content)
        self.assertIsInstance(result.content[0], types.TextContent)
        self.assertIn("Internal Integration Setup", result.content[0].text)
        self.assertEqual(
            result.structuredContent["guide_markdown"].splitlines()[0],
            "# Notion Agent Labbook Internal Integration Setup",
        )
        self.assertEqual(
            result.structuredContent["resource_uri"],
            "labbook://agent-labbook/setup-guide",
        )

    async def test_status_tool_returns_structured_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            async with stdio_client(self.server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        "notion_status", {"project_root": tmpdir}
                    )

        self.assertFalse(result.isError)
        self.assertEqual(result.structuredContent["integration"], "agent-labbook")
        self.assertEqual(
            result.structuredContent["available_env_var"],
            "NOTION_AGENT_LABBOOK_TOKEN",
        )
        self.assertIn("recommended_action", result.structuredContent)
        self.assertIn("secret_plan", result.structuredContent)
        self.assertIn("storage_options", result.structuredContent)
        self.assertIn("storage_choice_required", result.structuredContent)
        self.assertIn("recommended_local_command", result.structuredContent)
        self.assertFalse(result.structuredContent["storage_choice_required"])
        self.assertIn("binding_recommendation", result.structuredContent)
        self.assertIn("binding_options", result.structuredContent)
        self.assertTrue(result.content)

    async def test_prepare_tool_returns_urls_and_backend_choices(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            async with stdio_client(self.server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        "notion_prepare_internal_integration",
                        {"project_root": tmpdir, "open_browser": False},
                    )

        self.assertFalse(result.isError)
        self.assertEqual(
            result.structuredContent["notion_docs_url"],
            "https://developers.notion.com/guides/get-started/create-a-notion-integration",
        )
        self.assertEqual(
            result.structuredContent["notion_integrations_url"],
            "https://www.notion.so/my-integrations",
        )
        self.assertIn("storage_options", result.structuredContent)
        self.assertIn("recommended_local_command", result.structuredContent)
        self.assertEqual(
            result.structuredContent["recommended_next_action"],
            "notion_configure_internal_integration",
        )

    async def test_resources_expose_status_and_setup_guide(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root_query = parse.quote(tmpdir, safe="")
            async with stdio_client(self.server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    resources = await session.list_resources()
                    status_result = await session.read_resource(
                        f"labbook://agent-labbook/project/status?project_root={project_root_query}"
                    )
                    guide_result = await session.read_resource(
                        "labbook://agent-labbook/setup-guide"
                    )

        resource_uris = {str(resource.uri) for resource in resources.resources}
        self.assertIn("labbook://agent-labbook/project/status", resource_uris)
        self.assertIn("labbook://agent-labbook/setup-guide", resource_uris)
        self.assertTrue(status_result.contents)
        self.assertIn('"integration": "agent-labbook"', status_result.contents[0].text)
        self.assertTrue(guide_result.contents)
        self.assertIn("Internal Integration Setup", guide_result.contents[0].text)

    async def test_prompts_expose_guided_workflows(self) -> None:
        async with stdio_client(self.server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                prompts = await session.list_prompts()
                prompt = await session.get_prompt("notion_connect_project")
                use_bound_prompt = await session.get_prompt(
                    "notion_use_bound_resources"
                )

        prompt_names = {item.name for item in prompts.prompts}
        self.assertIn("notion_connect_project", prompt_names)
        self.assertIn("notion_use_bound_resources", prompt_names)
        self.assertTrue(prompt.messages)
        self.assertIsInstance(prompt.messages[0].content, types.TextContent)
        self.assertIn(
            "notion_prepare_internal_integration", prompt.messages[0].content.text
        )
        self.assertIn("keychain", prompt.messages[0].content.text)
        self.assertIn("configure-secret", prompt.messages[0].content.text)
        self.assertIn("notion_bind_resource_urls", prompt.messages[0].content.text)
        self.assertIn("notion_open_binding_browser", prompt.messages[0].content.text)
        self.assertIn("Never echo the secret back", prompt.messages[0].content.text)
        self.assertIn(
            "POST /v1/pages with markdown", use_bound_prompt.messages[0].content.text
        )


if __name__ == "__main__":
    unittest.main()
