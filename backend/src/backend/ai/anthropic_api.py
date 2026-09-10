"""Claude, behind the same interface as the OpenAI-shaped providers.

Everything else in `providers.py` speaks the OpenAI chat-completions shape, so
one implementation covers it. Claude does not: the system prompt is a
top-level field, tool calls and their results are content blocks inside the
messages rather than a parallel `tool_calls` array, and reasoning is a request
parameter rather than a string the model happens to return.

Rather than keep a translation of that by hand, the official SDK carries it,
and this module converts at the edges: OpenAI-shaped messages in, the
`{content, tool_calls, reasoning, usage}` the agent loop expects out. Nothing
above this file knows which shape a provider speaks.
"""
from __future__ import annotations

import json
from typing import Any

import anthropic

from ..sources.http import SourceError

#: Claude's own endpoint publishes no prices, so they live here and are shown
#: per million tokens like every other provider's. Dollars per token, as
#: strings, matching what the OpenAI-shaped providers return.
#:
#: A model absent from this table still works; its row simply shows no price.
PRICES: dict[str, tuple[str, str]] = {
    "claude-fable-5-1": ("0.00001", "0.00005"),
    "claude-fable-5": ("0.00001", "0.00005"),
    "claude-opus-5": ("0.000005", "0.000025"),
    "claude-opus-4-8": ("0.000005", "0.000025"),
    "claude-opus-4-7": ("0.000005", "0.000025"),
    "claude-opus-4-6": ("0.000005", "0.000025"),
    "claude-sonnet-5": ("0.000002", "0.00001"),
    "claude-sonnet-4-6": ("0.000003", "0.000015"),
    "claude-haiku-4-5": ("0.000001", "0.000005"),
}

#: Claude has no reasoning-free mode worth using. Older models take a fixed
#: token budget; current ones decide for themselves and take an effort level
#: instead. See the note in `_thinking`.
_BUDGET_MODELS = ("claude-haiku-4-5", "claude-sonnet-4-5", "claude-3")

#: Claude requires max_tokens. High enough not to truncate a long answer,
#: low enough that a non-streaming request stays inside the SDK's timeout.
MAX_TOKENS = 16000


def _client(api_key: str) -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(api_key=api_key)


def _fail(exc: Exception) -> SourceError:
    """Turn an SDK exception into the error the rest of the app handles."""
    if isinstance(exc, anthropic.AuthenticationError):
        return SourceError("ai", "Anthropic rejected the API key", 401)
    if isinstance(exc, anthropic.RateLimitError):
        return SourceError("ai", "Anthropic is rate limiting; wait a moment", 429)
    if isinstance(exc, anthropic.APIStatusError):
        return SourceError("ai", f"Anthropic: HTTP {exc.status_code} {str(exc)[:200]}")
    if isinstance(exc, anthropic.APIConnectionError):
        return SourceError("ai", f"Anthropic: {exc}")
    return SourceError("ai", f"Anthropic: {exc}")


def _thinking(model: str, thinking: str) -> dict[str, Any]:
    """Request fields for the reasoning setting, as this model expects them.

    Current models think adaptively and take an effort level; the fixed token
    budget they replaced is rejected outright. Older ones only understand the
    budget. "off" sends neither: explicitly disabling thinking on a model that
    reasons by default makes it write tool calls into its prose instead of
    calling them, which fails silently in a loop.
    """
    if thinking in ("", "off"):
        return {}
    if model.startswith(_BUDGET_MODELS):
        return {"thinking": {"type": "enabled", "budget_tokens": 4096}}
    return {
        "thinking": {"type": "adaptive", "display": "summarized"},
        "output_config": {"effort": thinking},
    }


def _tools(tool_schemas: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """OpenAI tool schemas to Claude's, which nests nothing."""
    out = []
    for t in tool_schemas or []:
        fn = t.get("function") or t
        out.append(
            {
                "name": fn.get("name"),
                "description": fn.get("description") or "",
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return out


def _messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Split the OpenAI-shaped history into a system prompt and Claude messages.

    Three differences do the work. The system prompt is not a message. An
    assistant turn that called tools carries `tool_use` blocks instead of a
    `tool_calls` array. And a tool's result is a `user` message holding
    `tool_result` blocks, keyed by the id of the call it answers, where OpenAI
    uses a `tool` role.

    Consecutive tool results are merged into one user message: Claude expects
    every result for a turn together, and splitting them teaches the model to
    stop calling tools in parallel.
    """
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []

    for m in messages:
        role = m.get("role")
        content = m.get("content") or ""

        if role == "system":
            if content:
                system_parts.append(content)
            continue

        if role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": m.get("tool_call_id") or "",
                "content": content or "(no output)",
            }
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
            continue

        if role == "assistant":
            blocks: list[dict[str, Any]] = []
            if content:
                blocks.append({"type": "text", "text": content})
            for call in m.get("tool_calls") or []:
                fn = call.get("function") or {}
                arguments = fn.get("arguments") or "{}"
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except ValueError:
                        arguments = {}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.get("id") or "call_0",
                        "name": fn.get("name"),
                        "input": arguments,
                    }
                )
            # An assistant turn with neither text nor calls has nothing to
            # replay, and an empty content list is rejected.
            if blocks:
                out.append({"role": "assistant", "content": blocks})
            continue

        out.append({"role": "user", "content": content or "(empty)"})

    return "\n\n".join(system_parts), out


async def list_models(api_key: str) -> list[dict[str, Any]]:
    """Models this key can reach, asked of Anthropic rather than hardcoded."""
    if not api_key:
        raise SourceError("ai", "no API key stored for Anthropic")
    try:
        async with _client(api_key) as client:
            found = [m async for m in client.models.list()]
    except Exception as exc:
        raise _fail(exc) from exc

    out = []
    for m in found:
        prompt_price, completion_price = PRICES.get(m.id, (None, None))
        out.append(
            {
                "id": m.id,
                "name": getattr(m, "display_name", None) or m.id,
                "context": getattr(m, "max_input_tokens", None),
                "prompt_price": prompt_price,
                "completion_price": completion_price,
                # Every Claude model served today takes tools and reasons, so
                # neither is a filter here the way it is for a router listing
                # hundreds of models from everyone.
                "tools_known": True,
                "reasoning": True,
            }
        )
    out.sort(key=lambda m: m["id"])
    return out


def _usage(usage: Any) -> dict[str, Any]:
    """Claude's token counts under the names the rest of the app reads."""
    if usage is None:
        return {}
    prompt = getattr(usage, "input_tokens", 0) or 0
    completion = getattr(usage, "output_tokens", 0) or 0
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


async def chat(
    model: str,
    api_key: str,
    messages: list[dict[str, Any]],
    tool_schemas: list[dict[str, Any]] | None = None,
    thinking: str = "off",
) -> dict[str, Any]:
    """One assistant turn, in the shape the agent loop expects."""
    if not api_key:
        raise SourceError("ai", "no API key stored for Anthropic")
    if not model:
        raise SourceError("ai", "no model chosen")

    system, history = _messages(messages)
    request: dict[str, Any] = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "messages": history,
        **_thinking(model, thinking),
    }
    if system:
        request["system"] = system
    if tool_schemas:
        request["tools"] = _tools(tool_schemas)

    try:
        async with _client(api_key) as client:
            resp = await client.messages.create(**request)
    except Exception as exc:
        raise _fail(exc) from exc

    # Safety classifiers can decline a request: HTTP 200, no usable content.
    # Reported rather than returned as an empty turn the loop would retry.
    if resp.stop_reason == "refusal":
        detail = getattr(resp, "stop_details", None)
        why = getattr(detail, "explanation", None) or getattr(detail, "category", None) or ""
        raise SourceError("ai", f"Claude declined this request. {why}".strip())

    text: list[str] = []
    reasoning: list[str] = []
    calls: list[dict[str, Any]] = []
    for block in resp.content:
        if block.type == "text":
            text.append(block.text)
        elif block.type == "thinking":
            # Empty unless display is "summarized"; the raw chain of thought is
            # never returned by any model.
            if getattr(block, "thinking", ""):
                reasoning.append(block.thinking)
        elif block.type == "tool_use":
            calls.append(
                {
                    "id": block.id,
                    "type": "function",
                    "function": {"name": block.name, "arguments": json.dumps(block.input)},
                }
            )

    return {
        "content": "".join(text),
        "tool_calls": calls,
        "reasoning": "\n".join(reasoning),
        "usage": _usage(resp.usage),
    }


async def complete_json(
    model: str, api_key: str, system: str, user: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """One completion parsed as JSON. Returns (answer, usage)."""
    if not api_key:
        raise SourceError("ai", "no API key stored for Anthropic")
    if not model:
        raise SourceError("ai", "no model chosen for this task")

    try:
        async with _client(api_key) as client:
            resp = await client.messages.create(
                model=model,
                max_tokens=MAX_TOKENS,
                system=f"{system}\n\nReply with JSON only, no prose.",
                messages=[{"role": "user", "content": user}],
            )
    except Exception as exc:
        raise _fail(exc) from exc

    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    # A fenced block is the usual way a refusal to answer bare JSON arrives.
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        answer = json.loads(text)
    except ValueError as exc:
        raise SourceError("ai", f"Anthropic did not return JSON: {text[:160]}") from exc
    return answer, _usage(resp.usage)
