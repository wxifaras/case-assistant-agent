"""Test 1: SharePoint data fetching without agent calls.

This test verifies that SharePoint sites are fetched correctly and prints
the data for inspection. It uses real credentials from .env.

Run from teams-messaging-app directory:
    python test_sharepoint_fetch.py
"""

import asyncio
import logging
from dotenv import load_dotenv

from sharepointService import SharePointService

# Load .env BEFORE creating any Azure services
# This ensures DefaultAzureCredential uses EnvironmentCredential (service principal)
# instead of falling through to az login
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def test_sharepoint_fetch():
    """Fetch and display all visible SharePoint sites."""
    service = SharePointService()
    try:
        logger.info("Fetching SharePoint sites...")
        sites = await service.get_sites()
        
        if not sites:
            logger.warning("No SharePoint sites found.")
            return
        
        logger.info(f"✓ Found {len(sites)} SharePoint sites:")
        for i, site in enumerate(sites, 1):
            logger.info(
                f"  [{i}] {site.display_name or site.name} "
                f"(id={site.id}, web_url={site.web_url})"
            )
        
        # Optionally fetch members for the first site to verify that too
        if sites:
            first_site = sites[0]
            logger.info(
                f"\nFetching members of first site: {first_site.display_name}..."
            )
            members = await service.get_site_members(first_site)
            logger.info(f"✓ Found {len(members)} members:")
            for member in members[:5]:  # Show first 5
                logger.info(
                    f"  - {member.display_name} ({member.email})"
                )
            if len(members) > 5:
                logger.info(f"  ... and {len(members) - 5} more")
    
    except Exception as exc:
        logger.exception("Failed to fetch SharePoint data")
        raise
    finally:
        await service.close()


if __name__ == "__main__":
    asyncio.run(test_sharepoint_fetch())
