"""
Agent Client - Handles interaction with the Microsoft Foundry agent.

This module contains the core logic for connecting to and communicating with
the agent published in Microsoft Foundry. It uses the OpenAI Responses API
to submit prompts and handle responses.
"""

import os
import logging
from typing import List, Dict, Any
from dotenv import load_dotenv

# Import Azure Identity and OpenAI client libraries
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import OpenAI

# Import function calling and image download libraries
import json
from urllib.error import URLError
from image_tools import download_commons_image, WikimediaRateLimitError



# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False

# Define the image download function
def download_image(commons_page_url: str) -> dict[str, str]:
    """Download a licensed computing history image from Wikimedia Commons."""
    return download_commons_image(commons_page_url)



class AgentClient:
    """Client for interacting with a Microsoft Foundry agent."""
    
    def __init__(self):
        """Initialize the agent client with authentication and endpoint."""
        project_endpoint = os.getenv("PROJECT_ENDPOINT")
        if not project_endpoint:
            raise ValueError("PROJECT_ENDPOINT not found in environment variables") 
        agent_name = os.getenv("AGENT_NAME")
        if not agent_name:
            raise ValueError("AGENT_NAME not found in environment variables")
        self.agent_endpoint = "{}/agents/{}/endpoint/protocols/openai".format(project_endpoint, agent_name)
    


        # Create OpenAI client authenticated with Azure credentials
        self.client = OpenAI(
            api_key=get_bearer_token_provider(
                DefaultAzureCredential(), 
                "https://ai.azure.com/.default"
            ),
            base_url=self.agent_endpoint,
            default_query={"api-version": "v1"}
        )
 

        
        # Maintain conversation history (last 3 exchanges)
        self.conversation_history: List[Dict[str, Any]] = []
        self.max_history = 3
    
    def send_message(self, user_message: str, image_url: str | None = None) -> str:
        """
        Send a message to the agent and return the response.
        
        Args:
            user_message: The text message from the user
            image_url: Optional validated PNG or JPEG data URL
            
        Returns:
            The agent's response text
        """
        user_entry = {
            "role": "user",
            "content": user_message if image_url is None else [
                {"type": "input_text", "text": user_message},
                {"type": "input_image", "image_url": image_url}
            ]
        }
        
        try:
            # Initialize assistant message variable
            assistant_message = ""



            # Send prompt with full conversation history and handle function calls
            pending_input = self.conversation_history + [user_entry]
            downloaded_images = []
            downloads_paused = False
            for round_number in range(6):
                response = self.client.responses.create(
                    input=pending_input,
                    tool_choice="none" if downloads_paused else "auto",
                )
                logger.info(
                    "Agent response %s: status=%s, output types=%s",
                    response.id, response.status, [item.type for item in response.output],
                )
                for item in response.output:
                    if item.type == "mcp_call":
                        logger.log(
                            logging.WARNING if item.error is not None else logging.INFO,
                            "MCP call: server=%s, tool=%s, status=%s, result_returned=%s, error=%s",
                            item.server_label, item.name, item.status,
                            item.output is not None, item.error is not None,
                        )
                    elif item.type == "mcp_list_tools":
                        logger.log(
                            logging.WARNING if item.error is not None else logging.INFO,
                            "MCP discovery: server=%s, error=%s",
                            item.server_label, item.error is not None,
                        )
                    elif item.type == "mcp_approval_request":
                        raise ValueError(
                            f"MCP approval required for {item.server_label}/{item.name}. "
                            "This client has no approval interface; review the agent's MCP approval setting."
                        )
                function_calls = [
                    item for item in response.output if item.type == "function_call"
                ]
                if not function_calls:
                    assistant_message = response.output_text
                    if not assistant_message.strip():
                        raise ValueError("The agent did not return a final text response.")
                    break
                if round_number == 5:
                    raise ValueError("The agent exceeded the function-calling limit.")

                pending_input.extend(
                    item.model_dump(exclude_none=True) for item in response.output
                )
                for call in function_calls:
                    try:
                        if call.name != "download_image":
                            raise ValueError(f"Unsupported function: {call.name}")
                        if downloads_paused:
                            raise ValueError("Downloads are paused. Do not retry or search for a replacement image.")
                        arguments = json.loads(call.arguments)
                        if (
                            not isinstance(arguments, dict)
                            or set(arguments) != {"commons_page_url"}
                            or not isinstance(arguments["commons_page_url"], str)
                        ):
                            raise ValueError("Expected one commons_page_url string argument.")
                        if downloaded_images:
                            raise ValueError("Only one image can be downloaded per request.")
                        result = download_image(arguments["commons_page_url"])
                        downloaded_images.append(result.pop("markdown"))
                    except WikimediaRateLimitError as error:
                        logger.warning("Image download rate limited: %s", error)
                        downloads_paused = True
                        result = {
                            "error": str(error),
                            "retryable": False,
                            "retry_after_seconds": error.retry_after_seconds,
                        }
                    except (ValueError, OSError, URLError) as error:
                        logger.warning("Image tool failed: %s", error)
                        result = {"error": str(error)}

                    pending_input.append({
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": json.dumps(result),
                    })

            if downloaded_images:
                assistant_message += "\n\n" + "\n\n".join(downloaded_images)

            
            # Commit the exchange only after the request succeeds.
            self.conversation_history.append(user_entry)
            self.conversation_history.append({
                "role": "assistant",
                "content": assistant_message
            })
            
            # Count user messages in history to enforce max_history limit
            user_message_count = sum(1 for msg in self.conversation_history 
                                    if isinstance(msg, dict) and msg.get("role") == "user")
            
            # Remove oldest exchanges if we have more than max_history
            while user_message_count > self.max_history:
                # Find and remove the first user message and its assistant response
                for i, msg in enumerate(self.conversation_history):
                    if isinstance(msg, dict) and msg.get("role") == "user":
                        self.conversation_history.pop(i)
                        if i < len(self.conversation_history) and self.conversation_history[i].get("role") == "assistant":
                            self.conversation_history.pop(i)
                        user_message_count -= 1
                        break
            
            return assistant_message
            
        except Exception as e:
            logger.exception("Error communicating with agent")
            raise RuntimeError("Unable to contact the agent. Please try again.") from e
    
    def reset_conversation(self):
        """Clear the conversation history."""
        self.conversation_history = []
