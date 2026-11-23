"""Exercise the completed client, including returned MCP activity."""

import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputItem, ResponseReasoningItem
from pydantic import TypeAdapter
import agent_client
import configure_agent


class LabClientTests(unittest.TestCase):
    module = agent_client

    def client(self):
        module = self.module
        with patch.dict(module.os.environ, {
            "PROJECT_ENDPOINT": "https://example.services.ai.azure.com/api/projects/test",
            "AGENT_NAME": "computing-history",
        }), patch.object(module, "get_bearer_token_provider"), patch.object(module, "DefaultAzureCredential"), \
                patch.object(module, "OpenAI") as factory, patch.object(configure_agent, "AIProjectClient") as project:
            client = module.AgentClient()
            module.AgentClient()
        project.assert_not_called()
        self.assertEqual(factory.call_count, 2)
        self.assertEqual(
            factory.call_args.kwargs["base_url"],
            "https://example.services.ai.azure.com/api/projects/test/agents/computing-history/endpoint/protocols/openai",
        )
        self.assertEqual(factory.call_args.kwargs["default_query"], {"api-version": "v1"})
        client.client = MagicMock()
        return client

    def test_direct_endpoint_function_call_loop_appends_verified_attribution(self):
        client = self.client()
        call = ResponseFunctionToolCall(
            type="function_call", name="download_image", call_id="call_1",
            arguments=json.dumps({"commons_page_url": "https://commons.wikimedia.org/wiki/File:Eniac.jpg"}),
        )
        reasoning = ResponseReasoningItem(type="reasoning", id="rs_1", summary=[])
        client.client.responses.create.side_effect = [
            types.SimpleNamespace(id="resp_1", status="completed", output=[reasoning, call], output_text=""),
            types.SimpleNamespace(id="resp_2", status="completed", output=[], output_text="Here is ENIAC."),
        ]
        with patch.object(self.module, "download_image", return_value={
            "markdown": '<img src="/downloads/' + "a" * 32 + '.jpg">Attribution: US Army',
            "license": "Public domain",
        }):
            text = client.send_message("Show ENIAC")
        self.assertIn("Attribution: US Army", text)
        for request in client.client.responses.create.call_args_list:
            self.assertEqual(set(request.kwargs), {"input", "tool_choice"})
            self.assertEqual(request.kwargs["tool_choice"], "auto")
        inputs = client.client.responses.create.call_args.kwargs["input"]
        self.assertTrue(any(item.get("type") == "reasoning" for item in inputs))
        output = next(item for item in inputs if item.get("type") == "function_call_output")
        self.assertEqual(output["call_id"], "call_1")
        self.assertEqual(json.loads(output["output"]), {"license": "Public domain"})
        self.assertEqual(client.conversation_history[-1]["content"], text)

    def test_upload_history_remains_supported(self):
        client = self.client()
        client.client.responses.create.side_effect = [
            types.SimpleNamespace(
                id=f"resp_{i}", status="completed", output=[], output_text=f"Answer {i}"
            ) for i in range(4)
        ]
        for i in range(4):
            client.send_message(f"Question {i}", "data:image/png;base64,example")
        self.assertEqual(len(client.conversation_history), 6)
        self.assertEqual(client.conversation_history[0]["content"][0]["text"], "Question 1")
        self.assertEqual(client.conversation_history[-2]["content"][1]["type"], "input_image")

    def test_image_then_wikipedia_followup_logs_mcp_call_and_preserves_context(self):
        client = self.client()
        image_call = ResponseFunctionToolCall(
            type="function_call", name="download_image", call_id="image_1",
            arguments=json.dumps({"commons_page_url": "https://commons.wikimedia.org/wiki/File:Eniac.jpg"}),
        )
        article_call = TypeAdapter(ResponseOutputItem).validate_python({
            "id": "mcp_1", "type": "mcp_call", "server_label": "wikipedia",
            "name": "wikipedia_get_article", "status": "completed",
            "arguments": '{"title":"ENIAC","language":"en","section_index":0}',
            "output": '{"content":"Synthetic introduction paragraph."}',
        })
        article_text = "# ENIAC\n\n## Introduction (from Wikipedia)\n\n> Synthetic introduction paragraph."
        client.client.responses.create.side_effect = [
            types.SimpleNamespace(id="resp_image_1", status="completed", output=[image_call], output_text=""),
            types.SimpleNamespace(id="resp_image_2", status="completed", output=[], output_text="Here is ENIAC."),
            types.SimpleNamespace(
                id="resp_wikipedia", status="completed", output=[article_call], output_text=article_text,
            ),
        ]
        with patch.object(self.module, "download_image", return_value={"markdown": "Verified ENIAC image."}) as download:
            client.send_message("Find and show me an image of ENIAC.")
            with self.assertLogs(self.module.logger, level="INFO") as logs:
                text = client.send_message("Now find a relevant Wikipedia article about it.")

        self.assertEqual(text, article_text)
        download.assert_called_once()
        request = client.client.responses.create.call_args.kwargs
        self.assertEqual(request["tool_choice"], "auto")
        self.assertIn("ENIAC", request["input"][0]["content"])
        self.assertIn("Verified ENIAC image.", request["input"][1]["content"])
        self.assertEqual(request["input"][-1]["content"], "Now find a relevant Wikipedia article about it.")
        output = "\n".join(logs.output)
        self.assertIn("resp_wikipedia", output)
        self.assertIn("MCP call: server=wikipedia, tool=wikipedia_get_article, status=completed", output)
        self.assertIn("result_returned=True, error=False", output)
        self.assertNotIn(article_call.arguments, output)
        self.assertNotIn("Synthetic introduction paragraph.", output)

    def test_discovery_does_not_claim_an_article_was_read(self):
        client = self.client()
        discovery = TypeAdapter(ResponseOutputItem).validate_python({
            "id": "discovery_1", "type": "mcp_list_tools", "server_label": "wikipedia", "tools": [],
        })
        client.client.responses.create.return_value = types.SimpleNamespace(
            id="resp_discovery", status="completed", output=[discovery], output_text="No article was retrieved.",
        )
        with self.assertLogs(self.module.logger, level="INFO") as logs:
            client.send_message("Find a Wikipedia article about ENIAC.")
        self.assertTrue(any("MCP discovery: server=wikipedia" in line for line in logs.output))
        self.assertFalse(any("MCP call:" in line for line in logs.output))

    def test_approval_request_is_reported_instead_of_accepting_unverified_text(self):
        client = self.client()
        approval = TypeAdapter(ResponseOutputItem).validate_python({
            "id": "approval_1", "type": "mcp_approval_request", "server_label": "wikipedia",
            "name": "wikipedia_get_article", "arguments": '{"title":"ENIAC","section_index":0}',
        })
        client.client.responses.create.return_value = types.SimpleNamespace(
            id="resp_approval", status="completed", output=[approval], output_text="Unverified introduction.",
        )
        with self.assertLogs(self.module.logger, level="ERROR") as logs:
            with self.assertRaises(RuntimeError) as error:
                client.send_message("Find a Wikipedia article about ENIAC.")
        self.assertIn("MCP approval required for wikipedia/wikipedia_get_article", str(error.exception.__cause__))
        self.assertIn("no approval interface", "\n".join(logs.output))
        self.assertEqual(client.conversation_history, [])

    def test_failed_discovery_is_visible_in_terminal(self):
        client = self.client()
        discovery = TypeAdapter(ResponseOutputItem).validate_python({
            "id": "discovery_1", "type": "mcp_list_tools", "server_label": "wikipedia",
            "tools": [], "error": "Server unavailable",
        })
        client.client.responses.create.return_value = types.SimpleNamespace(
            id="resp_failed", status="completed", output=[discovery], output_text="The server is unavailable.",
        )
        with self.assertLogs(self.module.logger, level="WARNING") as logs:
            client.send_message("Find a Wikipedia article about ENIAC.")
        self.assertTrue(any("MCP discovery: server=wikipedia, error=True" in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
