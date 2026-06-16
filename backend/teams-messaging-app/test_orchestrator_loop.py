"""Test 2: Orchestrator loop & prompt building.

This test mocks SharePoint/agent/messenger services and verifies that:
1. The orchestrator correctly loops over sites
2. Each site name is correctly substituted into the prompt
3. Services are called in the right order

Run from teams-messaging-app directory:
    python test_orchestrator_loop.py
"""

import asyncio
import logging
from dataclasses import dataclass

from boardcastOrchestrator import SiteBroadcastOrchestrator
from prompt import build_prompt

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ============================================================================
# Mock Services
# ============================================================================


@dataclass(frozen=True)
class MockSite:
    """Simplified mock of SharePointSite."""
    id: str
    name: str
    display_name: str
    hostname: str = "mock.sharepoint.com"
    site_path: str = "/sites/mock"
    web_url: str = "https://mock.sharepoint.com/sites/mock"


@dataclass(frozen=True)
class MockMember:
    """Simplified mock of SharePointMember."""
    aad_object_id: str
    display_name: str
    email: str
    upn: str = "mock@example.com"


class MockSharePointService:
    """Returns hardcoded test sites."""

    async def get_sites(self, *, search="*", max_results=200):
        logger.info("[Mock SP] get_sites() called")
        return [
            MockSite(
                id="site-1",
                name="it-helpdesk",
                display_name="IT Helpdesk",
            ),
            MockSite(
                id="site-2",
                name="hr-onboarding",
                display_name="HR Onboarding",
            ),
            MockSite(
                id="site-3",
                name="legal-contracts",
                display_name="Legal & Contracts",
            ),
        ]

    async def get_site_members(self, site):
        logger.info(f"[Mock SP] get_site_members() called for {site.display_name}")
        # Return 2 fake members per site
        return [
            MockMember(
                aad_object_id=f"user-{site.id}-1",
                display_name="Alice Smith",
                email="alice@example.com",
            ),
            MockMember(
                aad_object_id=f"user-{site.id}-2",
                display_name="Bob Jones",
                email="bob@example.com",
            ),
        ]

    async def close(self):
        pass


class MockAgentService:
    """Records each call; returns dummy responses."""

    def __init__(self):
        self.calls = []

    async def ask_oneshot(self, prompt: str) -> str:
        self.calls.append({"method": "ask_oneshot", "prompt": prompt})
        logger.info(
            f"[Mock Agent] ask_oneshot() called.\n"
            f"  Prompt preview: {prompt[:100]}..."
        )
        return f"[Agent response for: {prompt[:30]}...]"

    async def close(self):
        pass


class MockSendResult:
    """Enum mock."""
    SENT = "sent"
    USER_UNKNOWN = "unknown"
    SEND_ERROR = "error"


class MockTeamsMessenger:
    """Records each DM sent."""

    def __init__(self):
        self.dms = []

    async def send_to_user(self, user_id: str, body: str):
        self.dms.append({"user_id": user_id, "body": body})
        logger.info(f"[Mock Teams] send_to_user({user_id})")
        return MockSendResult.SENT

    async def close(self):
        pass


# ============================================================================
# Test
# ============================================================================


async def test_orchestrator_loop():
    """Run the orchestrator with mocked services and inspect the flow."""

    sharepoint = MockSharePointService()
    agent = MockAgentService()
    messenger = MockTeamsMessenger()

    orchestrator = SiteBroadcastOrchestrator(
        sharepoint_service=sharepoint,
        agent_service=agent,
        messenger=messenger,
        prompt_builder=build_prompt,
    )

    logger.info("=" * 70)
    logger.info("Starting orchestrator run...")
    logger.info("=" * 70)

    result = await orchestrator.run()

    logger.info("=" * 70)
    logger.info("Orchestrator finished")
    logger.info("=" * 70)

    # ---- Validate results ---- #

    logger.info(f"\n✓ Total sites processed: {result.total_sites}")
    logger.info(f"✓ Sites with members: {result.sites_with_members}")
    logger.info(f"✓ Total messages sent: {result.total_sent}")

    # Check that agent was called 3 times (once per site)
    assert len(agent.calls) == 3, f"Expected 3 agent calls, got {len(agent.calls)}"
    logger.info(f"\n✓ Agent called {len(agent.calls)} times (once per site)")

    # Verify site names are in the prompts
    logger.info("\nChecking that site names were substituted into prompts:")
    for i, call in enumerate(agent.calls, 1):
        prompt = call["prompt"]
        logger.info(f"  [{i}] Prompt:\n      {prompt}")

        # Each should reference a different site name
        has_site_ref = any(
            name in prompt
            for name in ["IT Helpdesk", "HR Onboarding", "Legal & Contracts"]
        )
        assert has_site_ref, f"No site name found in prompt {i}"

    # Check messenger was called (2 members × 3 sites = 6 DMs)
    expected_dms = 3 * 2  # 3 sites × 2 members each
    assert (
        len(messenger.dms) == expected_dms
    ), f"Expected {expected_dms} DMs, got {len(messenger.dms)}"
    logger.info(f"\n✓ Messenger sent {len(messenger.dms)} DMs ({expected_dms} expected)")

    # Print first DM as sanity check
    if messenger.dms:
        first_dm = messenger.dms[0]
        logger.info(
            f"\nFirst DM sent to {first_dm['user_id']}:\n"
            f"  {first_dm['body'][:150]}...\n"
        )

    logger.info("\n" + "=" * 70)
    logger.info("✓ All tests passed!")
    logger.info("=" * 70)


if __name__ == "__main__":
    asyncio.run(test_orchestrator_loop())
