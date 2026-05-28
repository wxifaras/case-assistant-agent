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

from microsoft_teams.apps import App
from microsoft_teams.apps.routing.activity_context import ActivityContext

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("teams-messaging-app")

app = App(skip_auth=True)


@app.on_message
async def on_message(ctx: ActivityContext) -> None:
    user_text = (ctx.activity.text or "").strip()
    logger.info("Received message: %s", user_text)
    await ctx.send(f"Echo: {user_text}")


async def main() -> None:
    port = int(os.getenv("PORT", "3978"))
    logger.info("Starting Teams messaging app on port %s", port)
    await app.start(port=port)


if __name__ == "__main__":
    asyncio.run(main())
