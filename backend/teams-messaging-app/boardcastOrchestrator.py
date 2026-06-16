"""Use-case orchestrator: SharePoint sites -> agent -> Teams members.

For each visible SharePoint site:
  1. build a prompt from the site name,
  2. ask the Foundry agent (one-shot, independent per site),
  3. DM the agent's reply to each member of that site via Teams.

Pure orchestration — all I/O is delegated to injected services. Errors at
one site do not abort the run; they are recorded in the returned
:class:`BroadcastResult` so the caller can present them.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from agentService import IAgentService
from sharepointService import ISharePointService, SharePointSite
from teamsMessenger import ITeamsMessenger, SendResult

logger = logging.getLogger(__name__)

PromptBuilder = Callable[[str], str]


# --------------------------------------------------------------------------- #
# Result models                                                               #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SiteBroadcastResult:
    site_name: str
    members_total: int
    sent: int
    skipped_unknown: int
    skipped_send_error: int
    error: str | None = None


@dataclass(frozen=True)
class BroadcastResult:
    total_sites: int
    sites_with_members: int
    per_site: list[SiteBroadcastResult] = field(default_factory=list)

    @property
    def total_sent(self) -> int:
        return sum(s.sent for s in self.per_site)

    @property
    def total_skipped(self) -> int:
        return sum(
            s.skipped_unknown + s.skipped_send_error for s in self.per_site
        )


# --------------------------------------------------------------------------- #
# Orchestrator                                                                #
# --------------------------------------------------------------------------- #


class SiteBroadcastOrchestrator:
    """Composes the SharePoint, agent, and Teams services into one workflow."""

    def __init__(
        self,
        *,
        sharepoint_service: ISharePointService,
        agent_service: IAgentService,
        messenger: ITeamsMessenger,
        prompt_builder: PromptBuilder,
    ) -> None:
        self._sharepoint = sharepoint_service
        self._agent = agent_service
        self._messenger = messenger
        self._build_prompt = prompt_builder

    async def run(self) -> BroadcastResult:
        sites = await self._sharepoint.get_sites()
        logger.info("SharePoint sites fetched: %d", len(sites))
        if not sites:
            logger.info("No SharePoint sites visible — nothing to broadcast")
            return BroadcastResult(total_sites=0, sites_with_members=0)

        per_site: list[SiteBroadcastResult] = []
        sites_with_members = 0

        for site in sites:
            result = await self._broadcast_to_site(site)
            per_site.append(result)
            if result.error is None and result.members_total > 0:
                sites_with_members += 1

        return BroadcastResult(
            total_sites=len(sites),
            sites_with_members=sites_with_members,
            per_site=per_site,
        )

    # ---- internals ---- #

    async def _broadcast_to_site(
        self, site: SharePointSite
    ) -> SiteBroadcastResult:
        site_label = site.display_name or site.name

        try:
            prompt = self._build_prompt(site_label)
            logger.info("Prompt sent to agent for SharePoint site: %s", site_label)
            message_body = await self._agent.ask_oneshot(prompt)
        except Exception as exc:
            logger.exception("Agent call failed for site %s", site_label)
            return SiteBroadcastResult(
                site_name=site_label,
                members_total=0,
                sent=0,
                skipped_unknown=0,
                skipped_send_error=0,
                error=f"agent error: {exc}",
            )

        try:
            members = await self._sharepoint.get_site_members(site)
            logger.info(
                "SharePoint members fetched for site %s: %d",
                site_label,
                len(members),
            )
        except Exception as exc:
            logger.exception("Member lookup failed for site %s", site_label)
            return SiteBroadcastResult(
                site_name=site_label,
                members_total=0,
                sent=0,
                skipped_unknown=0,
                skipped_send_error=0,
                error=f"member lookup error: {exc}",
            )

        if not members:
            return SiteBroadcastResult(
                site_name=site_label,
                members_total=0,
                sent=0,
                skipped_unknown=0,
                skipped_send_error=0,
            )

        body = f"Update for site '{site_label}':\n\n{message_body}"
        sent = 0
        skipped_unknown = 0
        skipped_send_error = 0

        for member in members:
            outcome = await self._messenger.send_to_user(
                member.aad_object_id, body
            )
            if outcome is SendResult.SENT:
                sent += 1
            elif outcome is SendResult.USER_UNKNOWN:
                skipped_unknown += 1
            else:
                skipped_send_error += 1

        return SiteBroadcastResult(
            site_name=site_label,
            members_total=len(members),
            sent=sent,
            skipped_unknown=skipped_unknown,
            skipped_send_error=skipped_send_error,
        )
