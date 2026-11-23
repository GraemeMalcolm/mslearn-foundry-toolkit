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

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)

# Define the image download function

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
            response = self.client.responses.create(
                input=self.conversation_history + [user_entry]
            )
            assistant_message = response.output_text




            
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
