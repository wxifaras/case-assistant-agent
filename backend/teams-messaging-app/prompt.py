"""Per-site prompt template for the broadcast workflow.

The orchestrator calls :func:`build_prompt` once per SharePoint site,
substituting the site's display name into the template. Edit
:data:`DEFAULT_PROMPT_TEMPLATE` to change the question.
"""

from __future__ import annotations

DEFAULT_PROMPT_TEMPLATE = (
    'You are writing a brief update for the members of the SharePoint site  '
    'named "{site_name}".\n\n'
    "Return an executive style summary. With what has been accomplished, status, and next steps.\n"
    
)


def build_prompt(site_name: str, template: str | None = None) -> str:
    """Render the per-site prompt.

    Raises ``ValueError`` if the template lacks a ``{site_name}`` placeholder.
    """
    tmpl = template or DEFAULT_PROMPT_TEMPLATE
    if "{site_name}" not in tmpl:
        raise ValueError(
            "Prompt template must contain a {site_name} placeholder."
        )
    return tmpl.format(site_name=site_name)
