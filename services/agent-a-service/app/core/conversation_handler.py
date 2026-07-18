"""
ConversationHandler — core AI orchestration logic for Agent A.

Responsibilities:
1. Receive an incoming message (text) in the context of an existing Conversation.
2. Retrieve relevant knowledge via RAGEngine.
3. Construct the full message history for GPT-4o.
4. Call GPT-4o and return the AI response.
5. Detect meeting-booking intent and set flags on the Conversation.
6. Bootstrap a new conversation for a freshly ingested lead.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from openai import AsyncOpenAI

from app.core.prompt_manager import PromptManager
from app.core.qualification_engine import LeadQualificationEngine
from app.core.rag_engine import RAGEngine
from app.models.conversation import (
    ChannelEnum,
    Conversation,
    ConversationMetrics,
    ConversationStatus,
    Message,
    MessageRole,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Intent detection — simple keyword regex (fast, no extra LLM call)
# ─────────────────────────────────────────────────────────────────────────────

_BOOKING_INTENT_PATTERN = re.compile(
    r"\b(book|schedule|set up|arrange|confirm|calendar|appointment|slot|meeting|call)\b",
    re.IGNORECASE,
)

# Maximum number of past messages included in the GPT context window
_MAX_HISTORY_MESSAGES = 20


def _build_lead_context(lead_data: dict[str, Any]) -> str:
    """Format lead_data as a compact context string for the system prompt."""
    lines = [
        f"Name:    {lead_data.get('name', 'Unknown')}",
        f"Email:   {lead_data.get('email', 'N/A')}",
        f"Phone:   {lead_data.get('phone', 'N/A')}",
        f"Company: {lead_data.get('company', 'N/A')}",
        f"Source:  {lead_data.get('source', 'N/A')}",
    ]
    if lead_data.get("notes"):
        lines.append(f"Notes:   {lead_data['notes']}")
    return "\n".join(lines)


def _build_rag_context(docs: list[dict[str, Any]]) -> str:
    """Format retrieved RAG documents into an inline context block."""
    if not docs:
        return ""
    parts = []
    for i, doc in enumerate(docs, start=1):
        parts.append(f"[{i}] (source: {doc.get('source', 'unknown')}, score: {doc.get('score', 0):.2f})\n{doc.get('content', '')}")
    return "\n\n".join(parts)


class ConversationHandler:
    """
    Orchestrates all AI logic for a single conversation.

    This class is stateless with respect to conversation data — callers pass
    a ``Conversation`` object in and receive a modified version back.  Persistence
    is handled by the calling router/service layer.

    Example::

        handler = ConversationHandler(prompt_manager, rag_engine, qual_engine, openai_client)
        response_text = await handler.handle_message(conversation, "What are your prices?", "tenant_123")
    """

    def __init__(
        self,
        prompt_manager: PromptManager,
        rag_engine: RAGEngine,
        qualification_engine: LeadQualificationEngine,
        openai_client: AsyncOpenAI,
        model: str = "gpt-4o",
    ) -> None:
        self._prompt_manager = prompt_manager
        self._rag_engine = rag_engine
        self._qualification_engine = qualification_engine
        self._openai = openai_client
        self._model = model

    # ------------------------------------------------------------------ #
    # Core message handling
    # ------------------------------------------------------------------ #

    async def handle_message(
        self,
        conversation: Conversation,
        new_message: str,
        tenant_id: str,
        lead_data: dict[str, Any] | None = None,
    ) -> str:
        """
        Process a new inbound message, call GPT-4o, and return the AI response.

        Side-effects on *conversation*:
        - Appends the new user Message.
        - Appends the assistant reply Message.
        - Sets ``meeting_booked = True`` and ``meeting_details`` if booking intent detected.
        - Updates ``updated_at``.

        Args:
            conversation: Current Conversation document (mutated in-place).
            new_message:  Raw text from the lead.
            tenant_id:    Tenant identifier (used for RAG collection lookup).
            lead_data:    Optional lead metadata dict for richer system prompt context.

        Returns:
            The AI assistant's response text.
        """
        if not new_message.strip():
            logger.warning("handle_message called with empty message; skipping.")
            return ""

        # 1. Append the user message to the conversation
        user_msg = Message(
            role=MessageRole.user,
            content=new_message,
        )
        conversation.messages.append(user_msg)

        # 2. Retrieve RAG context
        rag_docs: list[dict[str, Any]] = []
        try:
            rag_docs = await self._rag_engine.retrieve_context(
                tenant_id=tenant_id,
                query=new_message,
                top_k=5,
            )
        except Exception as exc:
            logger.warning("RAG retrieval failed (non-fatal): %s", exc)

        # 3. Build the system prompt
        rag_context_str = _build_rag_context(rag_docs)
        lead_ctx = _build_lead_context(lead_data or {})

        system_prompt = self._prompt_manager.get_system_prompt(
            tenant_id=tenant_id,
            scenario="initial_call" if len(conversation.messages) <= 2 else "faq",
            context={
                "lead_name": (lead_data or {}).get("name", "there"),
                "business_name": (lead_data or {}).get("business_name", "our company"),
                "business_context": (lead_data or {}).get("business_context", ""),
                "lead_context": lead_ctx,
                "retrieved_context": rag_context_str,
            },
        )

        # 4. Build the GPT messages list (system + sliding window of history)
        gpt_messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
        ]

        # Include last N messages (excluding the one we just appended, which is last)
        history = conversation.messages[-_MAX_HISTORY_MESSAGES:]
        for msg in history:
            role_val = msg.role.value if hasattr(msg.role, "value") else str(msg.role)
            # Map 'system' role messages to 'user' for history (they're internal notes)
            gpt_role = role_val if role_val in {"user", "assistant"} else "user"
            gpt_messages.append({"role": gpt_role, "content": msg.content})

        # 5. Call GPT-4o (non-streaming)
        try:
            response = await self._openai.chat.completions.create(
                model=self._model,
                messages=gpt_messages,  # type: ignore[arg-type]
                temperature=0.7,
                max_tokens=512,
            )
            ai_text: str = response.choices[0].message.content or ""
        except Exception as exc:
            logger.error("OpenAI completion failed: %s", exc, exc_info=True)
            ai_text = (
                "I'm sorry, I'm having a brief technical issue.  "
                "Please bear with me for a moment."
            )

        # 6. Append the assistant response
        assistant_msg = Message(
            role=MessageRole.assistant,
            content=ai_text,
        )
        conversation.messages.append(assistant_msg)
        conversation.updated_at = datetime.now(timezone.utc)
        conversation.status = ConversationStatus.active

        # 7. Detect meeting-booking intent in the AI response
        if not conversation.meeting_booked and _BOOKING_INTENT_PATTERN.search(ai_text):
            await self._handle_booking_intent(conversation)

        logger.debug(
            "handle_message complete | conv=%s messages=%d",
            conversation.id,
            len(conversation.messages),
        )
        return ai_text

    # ------------------------------------------------------------------ #
    # New lead bootstrapping
    # ------------------------------------------------------------------ #

    async def process_new_lead(
        self,
        lead_data: dict[str, Any],
        tenant_id: str,
        channel: ChannelEnum = ChannelEnum.voice,
    ) -> Conversation:
        """
        Create a brand-new Conversation and generate the agent's opening message.

        This is called by the Kafka consumer when a 'lead.created' event is received.

        Args:
            lead_data:  Lead metadata dict (name, email, phone, company, source, …).
            tenant_id:  Tenant identifier.
            channel:    Engagement channel.

        Returns:
            A new Conversation with the system and opening assistant messages populated.
        """
        lead_id = lead_data.get("id", lead_data.get("lead_id", "unknown"))

        conversation = Conversation(
            tenant_id=tenant_id,
            lead_id=lead_id,
            channel=channel,
            status=ConversationStatus.initiated,
            agent_version="1.0.0",
            prompt_version=self._prompt_manager.prompt_version,
        )

        # Build an opening system message (not shown to lead; used internally)
        system_prompt = self._prompt_manager.get_system_prompt(
            tenant_id=tenant_id,
            scenario="initial_call",
            context={
                "lead_name": lead_data.get("name", "there"),
                "business_name": lead_data.get("business_name", "our company"),
                "business_context": lead_data.get("business_context", ""),
                "lead_context": _build_lead_context(lead_data),
            },
        )
        system_msg = Message(role=MessageRole.system, content=system_prompt)
        conversation.messages.append(system_msg)

        # Generate opening message from GPT
        try:
            response = await self._openai.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": (
                            f"[Start the conversation with {lead_data.get('name', 'the lead')}. "
                            "Generate a warm, natural opening message from the AI assistant.]"
                        ),
                    },
                ],
                temperature=0.8,
                max_tokens=256,
            )
            opening_text: str = response.choices[0].message.content or (
                f"Hi {lead_data.get('name', 'there')}! This is an assistant from "
                f"{lead_data.get('business_name', 'our team')}. "
                "I noticed you recently reached out and I'd love to learn more about how we can help you."
            )
        except Exception as exc:
            logger.error("Failed to generate opening message: %s", exc, exc_info=True)
            opening_text = (
                f"Hi {lead_data.get('name', 'there')}! "
                "Thanks for your interest — I'm reaching out to learn more about your needs "
                "and see how we can help."
            )

        opening_msg = Message(role=MessageRole.assistant, content=opening_text)
        conversation.messages.append(opening_msg)
        conversation.status = ConversationStatus.active
        conversation.updated_at = datetime.now(timezone.utc)

        logger.info(
            "New conversation created | id=%s tenant=%s lead=%s channel=%s",
            conversation.id,
            tenant_id,
            lead_id,
            channel,
        )
        return conversation

    # ------------------------------------------------------------------ #
    # Conversation summarisation
    # ------------------------------------------------------------------ #

    async def generate_summary(self, conversation: Conversation) -> str:
        """
        Generate an AI summary of the conversation and store it on the object.

        Returns:
            The summary string.
        """
        transcript = conversation.build_transcript()
        if not transcript.strip():
            return "No conversation content to summarise."

        try:
            response = await self._openai.chat.completions.create(
                model=self._model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a CRM assistant. Summarise the following sales conversation "
                            "in 3-5 bullet points covering: key topics discussed, lead's main needs, "
                            "objections raised, and next steps agreed."
                        ),
                    },
                    {"role": "user", "content": transcript},
                ],
                temperature=0.3,
                max_tokens=300,
            )
            summary = response.choices[0].message.content or "Summary unavailable."
        except Exception as exc:
            logger.error("generate_summary error: %s", exc, exc_info=True)
            summary = "Summary generation failed due to a technical error."

        conversation.summary = summary
        return summary

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    async def _handle_booking_intent(self, conversation: Conversation) -> None:
        """
        Detect and extract meeting details when the AI response signals booking intent.
        Sets ``conversation.meeting_details`` if extraction succeeds.
        """
        transcript = conversation.build_transcript()
        try:
            meeting_info = await self._qualification_engine.extract_meeting_info(transcript)
            if meeting_info:
                conversation.meeting_booked = True
                conversation.meeting_details = meeting_info
                logger.info(
                    "Meeting booking intent detected | conv=%s details=%s",
                    conversation.id,
                    meeting_info,
                )
        except Exception as exc:
            logger.warning("Meeting info extraction failed: %s", exc)
