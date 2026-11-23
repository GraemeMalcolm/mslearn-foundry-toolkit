"""Exercise the exact learner snippets without completing the starter file."""

import ast
import json
import re
import sys
import textwrap
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))
from openai.types.responses import ResponseFunctionToolCall, ResponseReasoningItem
import configure_agent
from image_tools import WikimediaRateLimitError


def completed_client():
    lab = APP_DIR.parents[2] / "Instructions" / "Labs" / "02-use-tools.md"
    snippets = [textwrap.dedent(code) for code in
                re.findall(r"    ```python\n(.*?)    ```", lab.read_text(encoding="utf-8"), re.DOTALL)]
    imports, function, loop = snippets
    source = (APP_DIR / "agent_client.py").read_text(encoding="utf-8")
    send = next(node for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.FunctionDef) and node.name == "send_message")
    response = next(node for node in ast.walk(send) if isinstance(node, ast.Assign)
                    and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "response")
    output = next(node for node in ast.walk(send) if isinstance(node, ast.Assign)
                  and isinstance(node.value, ast.Attribute) and node.value.attr == "output_text")
    lines = source.splitlines(keepends=True)
    lines[response.lineno - 1:output.end_lineno] = [textwrap.indent(loop, "            ")]
    source = "".join(lines).replace(
        "# Import function calling and image download libraries",
        "# Import function calling and image download libraries\n" + imports,
    ).replace("# Define the image download function", "# Define the image download function\n" + function)
    module = types.ModuleType("lab_client_under_test")
    module.__file__ = str(APP_DIR / "agent_client.py")
    exec(compile(source, str(APP_DIR / "agent_client.py"), "exec"), module.__dict__)
    return module


class LabClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = completed_client()

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
            types.SimpleNamespace(output=[reasoning, call], output_text=""),
            types.SimpleNamespace(output=[], output_text="Here is ENIAC."),
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
            types.SimpleNamespace(output=[], output_text=f"Answer {i}") for i in range(4)
        ]
        for i in range(4):
            client.send_message(f"Question {i}", "data:image/png;base64,example")
        self.assertEqual(len(client.conversation_history), 6)
        self.assertEqual(client.conversation_history[0]["content"][0]["text"], "Question 1")
        self.assertEqual(client.conversation_history[-2]["content"][1]["type"], "input_image")

    def test_rate_limit_stops_batch_downloads_and_disables_further_tools(self):
        client = self.client()
        calls = [
            ResponseFunctionToolCall(
                type="function_call", name="download_image", call_id=f"call_{index}",
                arguments=json.dumps({"commons_page_url": "https://commons.wikimedia.org/wiki/File:Eniac.jpg"}),
            )
            for index in range(2)
        ]
        client.client.responses.create.side_effect = [
            types.SimpleNamespace(output=calls, output_text=""),
            types.SimpleNamespace(output=[], output_text="Wikimedia is rate limited; wait 120 seconds."),
        ]
        with patch.object(self.module, "download_image",
                          side_effect=WikimediaRateLimitError(120)) as download:
            text = client.send_message("Find and show me an image of ENIAC")
        download.assert_called_once()
        self.assertIn("wait 120 seconds", text)
        final_request = client.client.responses.create.call_args.kwargs
        self.assertEqual(final_request["tool_choice"], "none")
        outputs = [item for item in final_request["input"] if item.get("type") == "function_call_output"]
        self.assertEqual(json.loads(outputs[0]["output"])["retry_after_seconds"], 120)
        self.assertFalse(json.loads(outputs[0]["output"])["retryable"])
        self.assertIn("paused", json.loads(outputs[1]["output"])["error"])


if __name__ == "__main__":
    unittest.main()
