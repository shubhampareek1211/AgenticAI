"""Build bounded model context from whole conversational turns and a durable summary."""

import json
import logging
import math
import os

logger = logging.getLogger(__name__)


class ContextBudgetExceeded(ValueError):
    pass


def validate_summary(summary):
    """Reject invalid callback text before either UTF-8 estimation or a database checkpoint."""
    if not isinstance(summary, str) or not summary.strip() or "\x00" in summary:
        raise ContextBudgetExceeded("Conversation summarization failed. Retry the request.")
    try:
        summary.encode("utf-8")
    except UnicodeError:
        raise ContextBudgetExceeded(
            "Conversation summarization failed. Retry the request."
        ) from None


def estimate_tokens(
    messages: list[dict], tools: list[dict], model: str = "vertex_ai/gemini-3.5-flash-lite"
) -> int:
    """Estimate model tokens locally, with room for provider-specific framing."""
    # Keep the model-cost map local; counting must not make a provider request.
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    try:
        from litellm import token_counter

        count = token_counter(model=model, messages=messages, tools=tools)
        if isinstance(count, int) and count > 0:
            return math.ceil(count * 1.25) + 256
    except Exception as exc:  # noqa: BLE001 - Counting is advisory; use the safe byte bound on failure.
        logger.warning("Local token count unavailable (%s); using byte bound.", type(exc).__name__)
    # If counting is unavailable, prefer a safe upper bound over an undersized prompt.
    content = json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False)
    return len(content.encode("utf-8")) + 32 * len(messages)


def build_context(
    repo, current_turn, system_prompt, tools, budget, summarize, count_tokens=estimate_tokens
) -> list[dict]:
    """Retain exact turns while they fit; compact older turns only when the budget requires it."""
    turns = {}
    for message in repo.messages():
        if message.turn_number:
            turns.setdefault(message.turn_number, []).append(message.payload)
    completed = [
        (number, payloads)
        for number, payloads in turns.items()
        if number < current_turn
        and payloads[-1]["role"] == "assistant"
        and not payloads[-1].get("tool_calls")
    ]
    recent = [payload for _, payloads in completed[-10:] for payload in payloads]
    current = turns.get(current_turn, [])
    system = {"role": "system", "content": system_prompt}
    required = [system, *recent, *current]
    required_size = count_tokens(required, tools)
    if required_size > budget:
        raise ContextBudgetExceeded(
            "The recent conversation exceeds the context budget. Start a new conversation "
            "or ask the administrator to increase the context budget. Your history is saved."
        )
    conversation = repo.require()
    summary = conversation.summary or ""
    if summary:
        validate_summary(summary)
    older = [
        (number, payloads)
        for number, payloads in completed[:-10]
        if number > conversation.summary_through_turn
    ]

    def assemble(text, remaining):
        result = [system]
        if text:
            result.append({"role": "system", "content": "Earlier conversation summary:\n" + text})
        result.extend(payload for _, payloads in remaining for payload in payloads)
        result.extend(recent)
        result.extend(current)
        return result

    result = assemble(summary, older)
    if count_tokens(result, tools) <= budget:
        return result
    # Reserve only the summary's actual message framing; exact-fit inputs need no padding.
    reserve = budget - count_tokens(assemble(" ", []), tools)
    for index, (number, payloads) in enumerate(older):
        if reserve <= 0:
            break
        summary = summarize(summary, payloads, reserve)
        validate_summary(summary)
        if count_tokens(assemble(summary, []), tools) > budget:
            raise ContextBudgetExceeded(
                "The conversation summary exceeds the context budget. Start a new conversation "
                "or increase the context budget. Your history is saved."
            )
        repo.save_summary(summary, number)
        result = assemble(summary, older[index + 1 :])
        if count_tokens(result, tools) <= budget:
            return result
    raise ContextBudgetExceeded(
        "The recent conversation exceeds the context budget. Start a new conversation "
        "or increase the context budget. Your history is saved."
    )
