"""
PromptManager — manages versioned prompt templates for Agent A.

Each scenario has a dedicated template that is rendered with tenant-specific
business information, lead data, and the current date/time.  Prompt versions
are tracked so that conversations can be correlated to the exact prompt in use
at the time they occurred.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Prompt template library
# Each key maps to a (version, template_string) tuple.
# Templates use Python str.format_map()-compatible placeholders.
# ─────────────────────────────────────────────────────────────────────────────

_PROMPT_TEMPLATES: dict[str, dict[str, Any]] = {
    # ------------------------------------------------------------------ #
    # initial_call
    # ------------------------------------------------------------------ #
    "initial_call": {
        "version": "v1",
        "template": (
            "You are a friendly, professional AI assistant representing {business_name}. "
            "Today is {current_date} and the current time is {current_time}.\n\n"
            "You are reaching out to {lead_name}, a potential customer who expressed interest "
            "in {business_name}'s services/products.\n\n"
            "YOUR OBJECTIVES (in priority order):\n"
            "1. Greet {lead_name} warmly by name and briefly introduce yourself as an assistant "
            "   from {business_name}.\n"
            "2. Confirm this is a good time to talk (voice) or acknowledge their message (text).\n"
            "3. Ask natural, conversational qualifying questions to understand:\n"
            "   - What specific need or problem they are trying to solve.\n"
            "   - Their approximate budget range.\n"
            "   - Their desired timeline or urgency.\n"
            "   - Whether they are the primary decision-maker or need to consult others.\n"
            "4. Answer any questions they have using your knowledge of {business_name}.\n"
            "5. If the conversation is progressing well, offer to schedule a follow-up meeting "
            "   with a human specialist.\n\n"
            "TONE & STYLE GUIDELINES:\n"
            "- Be conversational, warm, and empathetic — not robotic or scripted.\n"
            "- Use the lead's first name occasionally to personalise the conversation.\n"
            "- Keep responses concise for voice channels; more detailed for text channels.\n"
            "- Do NOT reveal that you are an AI unless the lead directly and explicitly asks.\n"
            "  If asked, be honest: 'I am an AI assistant, but I'm here to help you just as "
            "  well as any human would.'\n"
            "- Never make promises you cannot keep; escalate to a human if needed.\n"
            "- Stay on topic; politely redirect off-topic conversations.\n\n"
            "BUSINESS CONTEXT:\n"
            "{business_context}\n\n"
            "LEAD CONTEXT:\n"
            "{lead_context}"
        ),
    },
    # ------------------------------------------------------------------ #
    # qualification
    # ------------------------------------------------------------------ #
    "qualification": {
        "version": "v1",
        "template": (
            "You are a lead qualification specialist for {business_name}. "
            "Today is {current_date}.\n\n"
            "Your role is to gather specific qualification information from {lead_name} "
            "in a natural, non-intrusive way.  You must determine:\n\n"
            "QUALIFICATION CRITERIA:\n"
            "1. BUDGET — Do they have an allocated budget?  What is the approximate range?\n"
            "2. TIMELINE — When are they looking to implement / purchase?\n"
            "3. AUTHORITY — Are they the decision-maker, or do others need to be involved?\n"
            "4. NEED — Do they have a specific, well-defined need that {business_name} can address?\n\n"
            "Ask ONE qualifying question at a time.  Listen carefully to the answers and probe "
            "deeper when responses are vague.  Be friendly and professional throughout.\n\n"
            "BUSINESS CONTEXT:\n"
            "{business_context}\n\n"
            "LEAD CONTEXT:\n"
            "{lead_context}"
        ),
    },
    # ------------------------------------------------------------------ #
    # faq
    # ------------------------------------------------------------------ #
    "faq": {
        "version": "v1",
        "template": (
            "You are a knowledgeable assistant for {business_name}. "
            "Today is {current_date}.\n\n"
            "Answer questions from {lead_name} accurately and helpfully, using only the "
            "information provided in the KNOWLEDGE BASE section below.  "
            "If the answer is not in the knowledge base, say: "
            "'That's a great question — let me connect you with one of our specialists who can "
            "give you a precise answer.'\n\n"
            "KNOWLEDGE BASE:\n"
            "{retrieved_context}\n\n"
            "BUSINESS CONTEXT:\n"
            "{business_context}\n\n"
            "LEAD CONTEXT:\n"
            "{lead_context}"
        ),
    },
    # ------------------------------------------------------------------ #
    # booking
    # ------------------------------------------------------------------ #
    "booking": {
        "version": "v1",
        "template": (
            "You are a scheduling assistant for {business_name}. "
            "Today is {current_date} and the current time is {current_time}.\n\n"
            "Your task is to book a follow-up meeting between {lead_name} and a specialist "
            "from {business_name}.\n\n"
            "BOOKING STEPS:\n"
            "1. Confirm the lead's interest in scheduling a meeting.\n"
            "2. Ask for their preferred days and times (mention that slots are available "
            "   Monday–Friday, 9 AM–6 PM in their timezone).\n"
            "3. Ask whether they prefer a video call, phone call, or in-person meeting.\n"
            "4. Confirm all details back to the lead before finalising.\n"
            "5. Inform them they will receive a calendar invitation at their email address.\n\n"
            "Be concise and efficient — the lead is ready to book; do not over-explain.\n\n"
            "BUSINESS CONTEXT:\n"
            "{business_context}\n\n"
            "LEAD CONTEXT:\n"
            "{lead_context}"
        ),
    },
    # ------------------------------------------------------------------ #
    # follow_up
    # ------------------------------------------------------------------ #
    "follow_up": {
        "version": "v1",
        "template": (
            "You are a follow-up specialist for {business_name}. "
            "Today is {current_date}.\n\n"
            "You are following up with {lead_name} after a previous interaction on "
            "{previous_contact_date}.  Here is a summary of what was discussed:\n\n"
            "{previous_summary}\n\n"
            "Your objectives for this follow-up:\n"
            "1. Re-engage the lead warmly, referencing the previous conversation.\n"
            "2. Check if their situation or needs have changed.\n"
            "3. Address any open questions from last time.\n"
            "4. Move the conversation forward — qualification, demo, or booking.\n\n"
            "BUSINESS CONTEXT:\n"
            "{business_context}\n\n"
            "LEAD CONTEXT:\n"
            "{lead_context}"
        ),
    },
}

# Active prompt version registry — maps scenario -> version string
# Updated by activate_prompt_version() at runtime.
_ACTIVE_VERSIONS: dict[str, str] = {
    scenario: data["version"] for scenario, data in _PROMPT_TEMPLATES.items()
}


class PromptManager:
    """
    Manages versioned system prompts for Agent A's conversation scenarios.

    Usage::

        pm = PromptManager()
        system_prompt = pm.get_system_prompt(
            tenant_id="t_123",
            scenario="initial_call",
            context={"lead_name": "Jane", "business_name": "Acme Corp", ...},
        )
    """

    # The semantic version of this PromptManager implementation
    prompt_version: str = "v1"

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def get_system_prompt(
        self,
        tenant_id: str,
        scenario: str,
        context: dict[str, Any],
    ) -> str:
        """
        Render and return a system prompt for the given scenario.

        Args:
            tenant_id: The tenant's identifier (used for future per-tenant overrides).
            scenario:  One of 'initial_call', 'qualification', 'faq', 'booking', 'follow_up'.
            context:   A dict of values to inject into the template.  Required keys vary by
                       scenario; common keys: lead_name, business_name, business_context,
                       lead_context.

        Returns:
            Fully rendered system prompt string.

        Raises:
            ValueError: If the scenario is unknown.
        """
        if scenario not in _PROMPT_TEMPLATES:
            raise ValueError(
                f"Unknown prompt scenario '{scenario}'. "
                f"Available: {list(_PROMPT_TEMPLATES.keys())}"
            )

        template_data = _PROMPT_TEMPLATES[scenario]
        template: str = template_data["template"]

        # Inject standard date/time values so templates don't need to include them
        now = datetime.now(timezone.utc)
        base_context: dict[str, Any] = {
            "current_date": now.strftime("%A, %B %d, %Y"),
            "current_time": now.strftime("%H:%M UTC"),
            "tenant_id": tenant_id,
            # Sensible defaults — callers should override these
            "business_name": "our company",
            "business_context": "",
            "lead_name": "there",
            "lead_context": "",
            "retrieved_context": "",
            "previous_contact_date": "",
            "previous_summary": "",
        }
        base_context.update(context)

        try:
            rendered = template.format_map(base_context)
        except KeyError as exc:
            logger.warning(
                "Prompt template for scenario '%s' is missing context key: %s",
                scenario,
                exc,
            )
            rendered = template  # return raw template as fallback

        logger.debug(
            "Rendered system prompt | tenant=%s scenario=%s version=%s chars=%d",
            tenant_id,
            scenario,
            template_data["version"],
            len(rendered),
        )
        return rendered

    def get_qualification_prompt(
        self,
        tenant_id: str,
        lead_data: dict[str, Any],
    ) -> str:
        """
        Convenience wrapper that builds a qualification system prompt from lead_data.

        Args:
            tenant_id: Tenant identifier.
            lead_data: Dict containing at minimum 'name', 'email', 'source', and optionally
                       'company', 'phone', 'notes', 'business_name', 'business_context'.

        Returns:
            Rendered qualification system prompt.
        """
        lead_context_lines = [
            f"Name: {lead_data.get('name', 'Unknown')}",
            f"Email: {lead_data.get('email', 'N/A')}",
            f"Phone: {lead_data.get('phone', 'N/A')}",
            f"Company: {lead_data.get('company', 'N/A')}",
            f"Source: {lead_data.get('source', 'N/A')}",
            f"Notes: {lead_data.get('notes', 'None')}",
        ]

        context = {
            "lead_name": lead_data.get("name", "there"),
            "business_name": lead_data.get("business_name", "our company"),
            "business_context": lead_data.get("business_context", ""),
            "lead_context": "\n".join(lead_context_lines),
        }
        return self.get_system_prompt(tenant_id, "qualification", context)

    # ------------------------------------------------------------------ #
    # Version management
    # ------------------------------------------------------------------ #

    def list_prompt_versions(self) -> list[dict[str, str]]:
        """Return a list of all registered prompt scenarios and their versions."""
        return [
            {
                "scenario": scenario,
                "version": data["version"],
                "active": _ACTIVE_VERSIONS.get(scenario) == data["version"],
            }
            for scenario, data in _PROMPT_TEMPLATES.items()
        ]

    def activate_prompt_version(self, scenario: str, version: str) -> bool:
        """
        Mark a specific version as active for a scenario.

        In production this would persist the active mapping to a database so that
        all worker replicas pick it up.  The current implementation is in-memory only.

        Returns:
            True if the version was found and activated, False otherwise.
        """
        if scenario not in _PROMPT_TEMPLATES:
            raise ValueError(f"Unknown scenario: {scenario}")

        if _PROMPT_TEMPLATES[scenario]["version"] == version:
            _ACTIVE_VERSIONS[scenario] = version
            logger.info("Activated prompt version '%s' for scenario '%s'.", version, scenario)
            return True

        logger.warning(
            "Prompt version '%s' not found for scenario '%s'. No change made.",
            version,
            scenario,
        )
        return False
