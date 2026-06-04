"""Test 3 (Optional): Prompt template substitution.

This is the simplest test—verifies the prompt building logic in isolation
without any async or network calls.

Run from teams-messaging-app directory:
    python test_prompt_building.py
"""

import logging
from prompt import build_prompt, DEFAULT_PROMPT_TEMPLATE

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def test_prompt_building():
    """Test prompt template substitution with various site names."""

    logger.info("Testing prompt template substitution...\n")

    test_cases = [
        "IT Helpdesk",
        "HR Onboarding",
        "Legal & Contracts",
        "DevOps Team",
        "Site with 'quotes' and special chars: @#$%",
    ]

    for site_name in test_cases:
        prompt = build_prompt(site_name)
        logger.info(f"Site: {site_name}")
        logger.info(f"  Generated prompt:\n{prompt}\n")
        
        # Verify the site name is in the prompt
        assert (
            site_name in prompt
        ), f"Site name '{site_name}' not found in prompt!"
        logger.info(f"  ✓ Site name correctly substituted\n")

    # Test that missing placeholder raises error
    logger.info("Testing error handling (no placeholder)...")
    bad_template = "This template has no site_name placeholder"
    try:
        build_prompt("TestSite", template=bad_template)
        assert False, "Should have raised ValueError"
    except ValueError as exc:
        logger.info(f"  ✓ Correctly raised ValueError: {exc}\n")

    logger.info("=" * 70)
    logger.info("✓ All prompt tests passed!")
    logger.info("=" * 70)


if __name__ == "__main__":
    test_prompt_building()
