import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from azure.ai.projects.models import MCPTool, PromptAgentDefinition, WebSearchPreviewTool
import configure_agent


class AgentConfigurationTests(unittest.TestCase):
    def project(self):
        definition = PromptAgentDefinition(
            model="gpt-test",
            instructions="Computing history only.",
            tools=[WebSearchPreviewTool()],
        )
        agent = SimpleNamespace(
            versions=SimpleNamespace(latest=SimpleNamespace(version="1", definition=definition)),
        )
        project = MagicMock()
        project.agents.get.return_value = agent

        def create_version(**kwargs):
            agent.versions.latest = SimpleNamespace(version="2", definition=kwargs["definition"])
            return agent.versions.latest

        project.agents.create_version.side_effect = create_version
        return project, agent

    def test_registers_once_preserves_agent_without_pinning_endpoint(self):
        project, agent = self.project()
        self.assertEqual(configure_agent.configure_agent(project, "computing-history"), "2")
        definition = agent.versions.latest.definition
        self.assertEqual(definition.model, "gpt-test")
        self.assertTrue(definition.instructions.startswith("Computing history only."))
        self.assertEqual([tool.type for tool in definition.tools], ["web_search_preview", "function"])
        project.agents.update_details.assert_not_called()

        self.assertEqual(configure_agent.configure_agent(project, "computing-history"), "2")
        project.agents.create_version.assert_called_once()
        project.agents.update_details.assert_not_called()
        self.assertEqual(definition.instructions.count(configure_agent.image_instructions), 1)

    def test_preserves_mcp_and_later_deployment_without_pinning_endpoint(self):
        project, agent = self.project()
        version = agent.versions.latest
        version.definition.instructions += "\nWikipedia article requests: retrieve the complete lead."
        mcp_tool = MCPTool(
            server_label="wikipedia",
            server_url="https://wikipedia.caseyjhand.com/mcp",
            require_approval="never",
        )
        version.definition.tools.append(mcp_tool)

        self.assertEqual(configure_agent.configure_agent(project, "computing-history"), "2")
        self.assertEqual(agent.versions.latest.definition.tools[1].as_dict(), mcp_tool.as_dict())
        self.assertIn("Wikipedia article requests:", agent.versions.latest.definition.instructions)
        agent.versions.latest.version = "3"
        original_definition = agent.versions.latest.definition.as_dict()

        self.assertEqual(configure_agent.configure_agent(project, "computing-history"), "3")
        self.assertEqual(agent.versions.latest.definition.as_dict(), original_definition)
        project.agents.create_version.assert_called_once()
        project.agents.update_details.assert_not_called()

    def test_rejects_missing_web_search_before_changing_agent(self):
        project, agent = self.project()
        agent.versions.latest.definition.tools = []
        with self.assertRaisesRegex(ValueError, "Enable Web search"):
            configure_agent.configure_agent(project, "computing-history")
        project.agents.create_version.assert_not_called()
        project.agents.update_details.assert_not_called()


if __name__ == "__main__":
    unittest.main()
