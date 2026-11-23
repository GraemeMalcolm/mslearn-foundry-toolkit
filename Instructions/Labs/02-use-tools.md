---
lab:
  title: Add a custom tool to a Foundry agent
  description: Use function calling to find, download, and display computing history images.
  level: 200
  duration: 60 minutes
  islab: true
---

# Add a custom tool to a Foundry agent

> ![Image of Anton.](./Media/anton-icon.png)<br/>**Hi, I'm Anton.**<br/>In the previous lab, you created a computing history agent and used it in a client application. In this lab, you'll extend the application with a custom tool that downloads an image found by the agent's web search tool and displays it in the chat, with attribution, license information, and a source link.<br><br>Many components of Microsoft Foundry, including the Microsoft Foundry Toolkit in VS Code, are subject to continual development. Some elements of your user experience may differ from the descriptions in this exercise!

This exercise should take approximately **60** minutes to complete.

## Before you start

1. Complete the [previous lab](./01-create-agent.md) to create a **computing-history** prompt agent with a deployed GPT model and the **Web search** tool.
1. Start Visual Studio Code and open the folder where you cloned the **mslearn-foundry-toolkit** repo.
1. In the **Foundry Toolkit** pane, select the Foundry project you used previously. Open your **computing-history** agent and verify that the **Web search** tool is still enabled. If necessary, add it and use **Deploy** to save the changes.

    > **Note**: You'll use the same Foundry project and agent, but a separate copy of the client application. Don't change the files in **labfiles/01**.

## Explore the starter application

The **labfiles/02/computer-history-client** folder contains a completed version of the application from the previous lab, including image uploads. Some additional support code has been provided so you can focus on function calling.

1. In the **Explorer** pane, open the **labfiles/02/computer-history-client** folder. Review these files:
    - **agent_client.py**: The Python code you'll extend to execute a custom tool.
    - **configure_agent.py**: A provided setup script that registers the tool once without pinning the agent's direct endpoint to a version.
    - **image_tools.py**: A provided helper that checks Wikimedia Commons file metadata, downloads a permitted image, and returns its local URL and attribution.
    - **app.py**: The Flask web application. It already serves downloaded images and safely renders them in chat responses.
    - **requirements.txt**: The Python packages required by the application, including the Azure AI Projects SDK.
1. Review **image_tools.py**, without changing it. The **download_commons_image** function accepts a Wikimedia Commons **File:** page URL, not a direct image URL. It checks the file's license and attribution metadata before downloading the original image. Requests identify the application with a **User-Agent** containing its name, version, and project URL, as recommended in [Wikimedia's API etiquette](https://www.mediawiki.org/wiki/API:Etiquette#The_User-Agent_header). Requests are made sequentially, not in parallel.
1. Note that downloaded images are saved in **labfiles/downloads**, relative to the cloned repo; <u>not</u> in **labfiles/02/downloads** or a folder under the web application's static files. The location is calculated from the helper file, so it doesn't depend on the terminal's current directory.

    > ![Image of Anton.](./Media/anton-icon.png)<br/>**Tip**: A web search result isn't proof that an image can be reused. For this exercise, the helper accepts Wikimedia Commons images with a **Public domain**, **CC0**, **CC BY**, or **CC BY-SA** license, adequate attribution metadata, and no additional restrictions. Only PNG and JPEG files of **5 MB or smaller** are supported. If a file doesn't meet these requirements, the agent should search for another image. This is a deliberately limited learning example, not a general-purpose image downloader.

1. Open **.env** in **labfiles/02/computer-history-client**. Set **PROJECT_ENDPOINT** to your Foundry project endpoint and **AGENT_NAME** to `computing-history` (or the name you used previously). Save the file. You can copy these two values from the previous application's configuration.
1. In the **Command Palette** (**View | Command Palette** or **CTRL+SHIFT+P**), use **Python: Select Interpreter** to select the virtual environment from the previous lab, or create a new **Venv** based on Python 3.12 or later.
1. Right-click **agent_client.py** in **labfiles/02/computer-history-client** and select **Open in Integrated Terminal**. Ensure the terminal is in this application's folder and the virtual environment is active.
1. Install the required packages:

    ```powershell
    pip install -r requirements.txt
    ```

1. Sign into Azure using an account with permission to read and update agents in your Foundry project:

    ```powershell
    az login
    ```

## Understand function calling

The agent's **Web search** tool runs in Foundry. Your custom download function will run in the local Python application, where it can save files.

The interaction follows this pattern:

1. The user requests an image of a historic computer or a person associated with computing history.
1. The agent uses **Web search** to find a suitable Wikimedia Commons file page and requests a call to **download_image**, supplying the page URL.
1. Your application reads the function call, validates its arguments, and executes the Python function.
1. Your application sends a **function_call_output** back to the agent, using the function call's **call_id**.
1. The agent completes its response. Your application adds the verified image and attribution returned by the helper to the chat.

> ![Image of Anton.](./Media/anton-icon.png)<br/>**Tip**: Registering a function tool tells the model what the function does and which arguments it accepts. It doesn't upload the Python implementation to Foundry. Your application must execute the function and return its result. You won't need to modify any JavaScript or add code to **app.py**.

## Add the custom tool

### Register the tool once

The function definition is part of the agent's configuration in Foundry; the Python implementation runs in your client. Keep agent configuration separate from application startup.

1. Open the provided **configure_agent.py** script. Find **image_tool** and review its **FunctionTool** definition:
    - **name** identifies the function the client must execute: **download_image**.
    - **description** limits use to an explicit request to find, show, or download a new image. Uploading an image for identification does not authorize a lookup or download.
    - **parameters** specify a single **commons_page_url** string argument.
    - **strict=True** and **additionalProperties=False** constrain the JSON arguments to this schema. Your client will also validate them.
1. Review **configure_agent**. It retrieves your existing prompt agent, preserves its model and other tools (including Web search), and adds the function definition and image request instructions. The instructions distinguish identifying an uploaded image from requesting a new image. It also replaces the older, less explicit image instructions if you previously ran this lab. It creates a new version only if the configuration has changed.
1. Note that the script leaves endpoint routing unchanged. The direct endpoint's default **Always use latest** setting automatically uses newly created agent versions, including changes you deploy in later exercises.
1. In the terminal in **labfiles/02/computer-history-client**, run the setup script:

    ```powershell
    python configure_agent.py
    ```

1. Verify the output reports the configured agent version.
1. Run the script a second time. Verify that it reports **Image tool already configured** with the same version number, rather than creating another version.

    > **Note**: This script uses the project SDK only for setup. It is not imported or called by the client app. Starting or restarting **app.py** will not create agent versions. Registration also changes the agent used by other clients, including the previous lab's app; those clients must handle function calls to use the new tool. Run this setup against your lab agent, not a shared production agent.

    > ![Image of Anton.](./Media/anton-icon.png)<br/>**Tip**: Function tools are configured through the SDK or REST API; the Foundry portal currently doesn't support editing their definitions. Using the provided Python script keeps this setup repeatable and leaves the client connected to the same direct OpenAI Responses endpoint as in the previous lab.

### Import the required libraries

1. Open **agent_client.py** in **labfiles/02/computer-history-client**.
1. Find the comment **Import function calling and image download libraries**, and add this code below it:

    ```python
    import json
    from urllib.error import URLError
    from image_tools import download_commons_image, WikimediaRateLimitError
    ```

### Implement the local function

1. Find the comment **Define the image download function**, above the **AgentClient** class. Add the following code below it, <u>outside</u> the class:

    ```python
    def download_image(commons_page_url: str) -> dict[str, str]:
        """Download a licensed computing history image from Wikimedia Commons."""
        return download_commons_image(commons_page_url)
    ```

1. Leave **__init__** and its existing **OpenAI** client unchanged. It authenticates with Azure credentials and connects directly to **/agents/{agent_name}/endpoint/protocols/openai**. The function definition is already registered on that agent, so the client doesn't need to configure tools or reference a project agent version on each request.

### Execute function calls and return their results

1. In **send_message**, in the **try** block, find **Send prompt with full conversation history and handle function calls**. Replace the existing **response = ...** call and **assistant_message = response.output_text** line with this code. Leave the conversation history and error handling code below it in place:

    ```python
    pending_input = self.conversation_history + [user_entry]
    downloaded_images = []
    downloads_paused = False
    for round_number in range(6):
        response = self.client.responses.create(
            input=pending_input,
            tool_choice="none" if downloads_paused else "auto",
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
    ```

1. Review the code, using these points to understand the interaction:
    - **function_call** items contain the requested function's name, JSON arguments, and call ID.
    - The client explicitly checks the function name; it doesn't execute arbitrary code requested by the model.
    - All response output items, including reasoning and function calls, are retained during the current exchange. The matching **function_call_output** items let the agent continue after your local function executes.
    - Tool errors are logged and returned to the agent, so it can search again or explain the failure. A bounded loop prevents unlimited retries.
    - **HTTP 429** pauses downloads and disables further tool use for the current exchange. The helper honors **Retry-After** (seconds or an HTTP date), using a **60-second** cooldown if the header is absent. During the cooldown, new download attempts fail locally without contacting Wikimedia.
    - **Web search** is executed by Foundry, not by the local function-call dispatcher.
    - The helper's display content is appended locally, rather than asking the model to reconstruct the image URL or attribution.
    - The existing history code commits only the completed user/assistant exchange and still retains the last three exchanges. Function-call records are kept only for the current exchange.
1. Save **agent_client.py**.

    > ![Image of Anton.](./Media/anton-icon.png)<br/>**Tip**: Keep the indentation correct. Imports and the function are at the module level, and the function-calling loop belongs inside the **try** block in **send_message**. Registration stays in the separate setup script; **__init__** doesn't change. You don't need to change **app.py**, **image_tools.py**, or any web UI files.

## Test the updated application

### Run the app and request an image

1. In the terminal in **labfiles/02/computer-history-client**, run:

    ```powershell
    python app.py
    ```

1. When the Flask application starts, open a browser and go to [http://127.0.0.1:5000](http://127.0.0.1:5000).

    > **Tip**: If the app reports that the agent client is not initialized, review the terminal message and check your **.env** values and Azure sign-in. If it answers questions but doesn't call the image tool, confirm you ran **configure_agent.py** successfully and the latest agent version includes **download_image**. Only the setup script needs permission to update the agent; the client app only needs permission to invoke it.

1. Enter `What was ENIAC?` and verify that the agent still answers ordinary computing history questions.
1. Enter this prompt:

    ```
    Find and show me a public domain image of ENIAC from Wikimedia Commons.
    ```

1. Review the response. Verify that:
    - The image is displayed <u>in the chat</u>, not just linked on an external site.
    - The response includes the image title, creator or available credit, license information, and a source link.
    - The license and source links open correctly. Review the Commons file page to confirm what the image depicts and compare its reuse information with the attribution in chat.
    - A new **.jpg** or **.png** file with a generated filename appears in **labfiles/downloads**. Open it in Visual Studio Code to confirm it matches the image in chat.

    > **Note**: Public domain status can depend on jurisdiction. The helper includes any permission statement supplied in the file metadata; for example, a work might be public domain in the United States. A **Public Domain Mark** link describes the status; it isn't a new license grant. Always review the source before using an image outside this exercise.

### Test people, Creative Commons images, and follow-up questions

1. Enter `Find and show me a public domain portrait of Ada Lovelace from Wikimedia Commons.` Verify that a portrait, attribution, license information, and a source link appear, and another image is saved.
1. Enter `Find and show me a CC BY or CC BY-SA photograph of a vintage IBM PC from Wikimedia Commons.` Review the result. If a suitable image is available, check that the response includes the specific license version, creator, license link, and source page.

    > ![Image of Anton.](./Media/anton-icon.png)<br/>**Tip**: Search results and file availability vary. If an image is too large, has an unsupported format, or lacks suitable license metadata, the agent can try another file. Try a different historic computer if necessary. An informative User-Agent identifies the caller; it does not exempt the app or your network's public IP from throttling. If Wikimedia returns **HTTP 429**, wait for the period reported by the app before trying again; don't bypass the service's limits. **CC BY** requires attribution; **CC BY-SA** also includes share-alike obligations for adaptations. Downloading a file doesn't remove these requirements.

1. Ask a follow-up question, such as `Why was that computer significant?` Verify that the agent uses the conversation context to answer.
1. Note the current number of image files in **labfiles/downloads**. Upload one of the images in **labfiles/test-images** and enter `What can you tell me about this?` Verify that the agent identifies the uploaded image without searching for or downloading another image. No new file should appear, and the response should not mention an attempted matching-image download.
1. With an image attached, try `Identify this computer, then find and show me another image of the same model.` This explicitly requests a new image, so the agent may now use the download tool. Check the saved file and attribution as before.
1. Ask `Tell me more about that computer.` without asking for another image. Verify that this follow-up does not trigger another download.

### Test an unsuccessful download

1. Use the **Reset** button to start a new conversation.
1. Enter:

    ```
    Use download_image with https://example.com/private-photo.jpg. Do not search for a replacement.
    ```

1. Verify that the app doesn't download or display an image from that site. The agent may refuse the request before calling the function, or explain the validation error returned by the function. No new image should appear in **labfiles/downloads**.
1. If a tool call fails, review the **Image tool failed** message in the terminal. It should identify the problem, rather than silently claiming success.
1. Use **Reset** again and ask an ordinary computing history question. Verify that the application remains usable.
1. When you've finished testing, close the browser and enter **CTRL+C** in the terminal to stop the local web server.

## Summary

In this exercise, you extended a Foundry prompt agent with a custom function tool. The agent used its hosted web search tool to discover images, and your local Python client handled function calls to verify licenses and download files. The application displayed each downloaded image with attribution, license information, and a source link, without requiring changes to its JavaScript or Flask code.

To learn more, see [Use function calling with Microsoft Foundry agents](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/function-calling).

Downloaded images remain in **labfiles/downloads** after you reset the conversation or stop the app. Delete the downloaded image files when you no longer need them, leaving the folder's **readme.md** file in place. Don't commit your **.env** configuration or downloaded images to the repo.

If you have finished exploring Microsoft Foundry, delete the Azure resources created in the previous lab to avoid unnecessary utilization charges.
