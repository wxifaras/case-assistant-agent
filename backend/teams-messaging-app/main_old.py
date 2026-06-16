"""Minimal Teams messaging app entrypoint for local Agents Playground testing.

Run:
    python main.py

Then in a separate terminal:
    agentsplayground -e http://localhost:3978/api/messages -c msteams

`skip_auth=True` is set so the local Playground can call the messaging endpoint
without bot app credentials. Do not use this setting outside local testing.
"""

import asyncio
import logging
import os

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv
from microsoft_teams.apps import App
from microsoft_teams.apps.routing.activity_context import ActivityContext

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("teams-messaging-app")



PROJECT_ENDPOINT = os.getenv("PROJECT_ENDPOINT")
AGENT_NAME = os.getenv("AGENT_NAME")  # e.g. "Coordinator-Agent"
PORT = int(os.getenv("PORT", "3978"))

if not PROJECT_ENDPOINT or not AGENT_NAME:
    raise RuntimeError(
        "PROJECT_ENDPOINT and AGENT_NAME must be set in the environment "
        "(see .env)."
    )

# ---------------------------------------------------------------------------
# Foundry / OpenAI clients
# ---------------------------------------------------------------------------
# DefaultAzureCredential resolves credentials from (in order): env vars,
# managed identity, `az login`, VS Code, etc.
project_client = AIProjectClient(
    endpoint=PROJECT_ENDPOINT,
    credential=DefaultAzureCredential(),
)
# OpenAI-compatible client used to drive the agent via the Responses API.
openai_client = project_client.get_openai_client()

# Reference passed to every responses.create call so the conversation is
# served by the right Foundry agent.
AGENT_REFERENCE = {"name": AGENT_NAME, "type": "agent_reference"}

# Map Teams conversation id -> Foundry conversation id.
# In-memory only — swap for Redis / a DB if you run multiple replicas.
conversation_map: dict[str, str] = {}


def _get_or_create_conversation(teams_conversation_id: str) -> str:
    """Return the Foundry conversation id bound to this Teams chat,
    creating one on first contact."""
    existing = conversation_map.get(teams_conversation_id)
    if existing:
        return existing
    conv = openai_client.conversations.create()
    conversation_map[teams_conversation_id] = conv.id
    logger.info(
        "Created Foundry conversation %s for Teams chat %s",
        conv.id,
        teams_conversation_id,
    )
    return conv.id


def _ask_agent(foundry_conversation_id: str, user_text: str) -> str:
    """Send the user's message to the agent and return its text reply.

    Synchronous — call via ``asyncio.to_thread`` from async handlers.
    """
    response = openai_client.responses.create(
        conversation=foundry_conversation_id,
        extra_body={"agent_reference": AGENT_REFERENCE},
        input=user_text,
    )
    # `output_text` is the convenience accessor on the Responses object.
    return response.output_text or "(no response from agent)"


# ---------------------------------------------------------------------------
# Teams app
# ---------------------------------------------------------------------------
app = App(skip_auth=True)


@app.on_message
async def on_message(ctx: ActivityContext) -> None:
    user_text = (ctx.activity.text or "").strip()
    if not user_text:
        return

    teams_conversation_id = ctx.activity.conversation.id
    logger.info("Message from %s: %s", teams_conversation_id, user_text)

    foundry_conversation_id = _get_or_create_conversation(
        teams_conversation_id
    )

    try:
        reply = await asyncio.to_thread(
            _ask_agent, foundry_conversation_id, user_text
        )
    except Exception:
        logger.exception("Error calling Foundry agent")
        reply = "Sorry — I ran into a problem reaching the agent."

    await ctx.send(reply)


async def main() -> None:
    logger.info("Starting Teams app on port %s", PORT)
    logger.info("Connected to Foundry agent '%s'", AGENT_NAME)
    await app.start(port=PORT)


if __name__ == "__main__":
    asyncio.run(main())
