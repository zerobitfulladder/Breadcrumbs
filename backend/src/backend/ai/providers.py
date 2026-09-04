"""Chat completions across providers, through one OpenAI-compatible client.

OpenRouter, DeepSeek and Gemini all expose an OpenAI-shaped endpoint, so a
single implementation covers them and adding another is a base URL. Keeping
this uniform is what lets any task run on any model.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from ..sources.http import SourceError


@dataclass(frozen=True)
class Provider:
    key: str
    label: str
    base_url: str
    setting: str          # which stored setting holds the key
    docs: str


PROVIDERS: dict[str, Provider] = {
    "openrouter": Provider(
        "openrouter", "OpenRouter",
        "https://openrouter.ai/api/v1",
        "ai_openrouter_key",
        "https://openrouter.ai/keys",
    ),
    "gemini": Provider(
        "gemini", "Google Gemini",
        "https://generativelanguage.googleapis.com/v1beta/openai",
        "ai_gemini_key",
        "https://aistudio.google.com/apikey",
    ),
    "deepseek": Provider(
        "deepseek", "DeepSeek",
        "https://api.deepseek.com/v1",
        "ai_deepseek_key",
        "https://platform.deepseek.com/api_keys",
    ),
}


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        # OpenRouter attributes traffic with these; harmless elsewhere.
        "HTTP-Referer": "https://github.com/local/breadcrumbs",
        "X-Title": "Breadcrumbs",
    }


async def list_models(
    client: httpx.AsyncClient, provider: str, api_key: str
) -> list[dict[str, Any]]:
    """Models the key can actually reach, asked of the provider itself.

    Better than a hardcoded list, which goes stale and cannot know what a
    particular key is entitled to.
    """
    p = PROVIDERS.get(provider)
    if not p:
        raise SourceError("ai", f"unknown provider '{provider}'")
    if not api_key:
        raise SourceError("ai", f"no API key stored for {p.label}")

    try:
        resp = await client.get(f"{p.base_url}/models", headers=_headers(api_key), timeout=45.0)
    except httpx.HTTPError as exc:
        raise SourceError("ai", f"{p.label}: {exc}") from exc
    if resp.status_code == 401:
        raise SourceError("ai", f"{p.label} rejected the API key", 401)
    if resp.status_code >= 400:
        raise SourceError("ai", f"{p.label}: HTTP {resp.status_code} {resp.text[:160]}")

    out = []
    for m in (resp.json().get("data") or []):
        ident = m.get("id")
        if not ident:
            continue
        params = m.get("supported_parameters")
        # OpenRouter says which parameters each model accepts. Where it does,
        # models without tool support are dropped: the assistant is useless on
        # one, and picking it would fail silently at the first tool call.
        if isinstance(params, list) and "tools" not in params:
            continue
        pricing = m.get("pricing") or {}
        out.append(
            {
                "id": ident,
                "name": m.get("name") or ident,
                "context": m.get("context_length"),
                # Dollars per token, as strings. Shown per million, which is
                # how every provider quotes them.
                "prompt_price": pricing.get("prompt"),
                "completion_price": pricing.get("completion"),
                "tools_known": isinstance(params, list),
                # Whether the provider says this model can be asked to reason.
                # Unknown (None) where the provider does not publish the list.
                "reasoning": (
                    bool({"reasoning", "reasoning_effort"} & set(params))
                    if isinstance(params, list)
                    else None
                ),
            }
        )
    out.sort(key=lambda m: m["id"])
    return out


async def list_endpoints(
    client: httpx.AsyncClient, provider: str, model: str, api_key: str
) -> list[dict[str, Any]]:
    """The upstream providers serving one OpenRouter model, with their prices.

    OpenRouter is a router: `anthropic/claude-sonnet-4.5` is served by Anthropic,
    Google, Azure and Bedrock at different prices and context limits, and by
    default it picks for you. Listing them is what makes pinning one a decision
    rather than a guess.

    Only OpenRouter has this shape. Everyone else serves their own models, so
    the list is empty and the UI hides the choice.
    """
    if provider != "openrouter":
        return []
    p = PROVIDERS["openrouter"]
    if not model:
        return []

    try:
        resp = await client.get(
            f"{p.base_url}/models/{model}/endpoints",
            headers=_headers(api_key), timeout=45.0,
        )
    except httpx.HTTPError as exc:
        raise SourceError("ai", f"{p.label}: {exc}") from exc
    if resp.status_code == 404:
        return []
    if resp.status_code >= 400:
        raise SourceError("ai", f"{p.label}: HTTP {resp.status_code} {resp.text[:160]}")

    data = resp.json().get("data") or {}
    out = []
    for e in data.get("endpoints") or []:
        name = e.get("provider_name")
        if not name:
            continue
        pricing = e.get("pricing") or {}
        params = e.get("supported_parameters")
        out.append(
            {
                "provider_name": name,
                "context": e.get("context_length"),
                "prompt_price": pricing.get("prompt"),
                "completion_price": pricing.get("completion"),
                "quantization": e.get("quantization"),
                "uptime": e.get("uptime_last_30m"),
                # An endpoint without tool support cannot run the assistant,
                # the same rule the model list already applies.
                "tools": ("tools" in params) if isinstance(params, list) else None,
            }
        )
    # Cheapest first: the usual reason to override the automatic choice.
    out.sort(key=lambda e: float(e["prompt_price"] or 0) or float("inf"))
    return out


async def chat(
    client: httpx.AsyncClient,
    provider: str,
    model: str,
    api_key: str,
    messages: list[dict[str, Any]],
    tool_schemas: list[dict[str, Any]] | None = None,
    thinking: str = "off",
    route: str = "",
) -> dict[str, Any]:
    """One assistant turn. Returns {content, tool_calls, usage}.

    `thinking` is off, low, medium or high. It is sent as `reasoning_effort`,
    the OpenAI-compatible spelling all three providers accept. A model that
    does not reason rejects it, so the call is retried without it rather than
    failing the turn.
    """
    p = PROVIDERS.get(provider)
    if not p:
        raise SourceError("ai", f"unknown provider '{provider}'")
    if not api_key:
        raise SourceError("ai", f"no API key stored for {p.label}")

    payload: dict[str, Any] = {"model": model, "messages": messages, "temperature": 0.2}
    if tool_schemas:
        payload["tools"] = tool_schemas
        payload["tool_choice"] = "auto"
    if thinking and thinking != "off":
        payload["reasoning_effort"] = thinking
    if route and provider == "openrouter":
        # Pin the upstream provider. Fallbacks stay off on purpose: silently
        # serving the turn from somewhere else would defeat the point of
        # choosing, and the error says the pinned one is unavailable.
        payload["provider"] = {"order": [route], "allow_fallbacks": False}

    async def post(body: dict[str, Any]) -> httpx.Response:
        try:
            return await client.post(
                f"{p.base_url}/chat/completions",
                headers=_headers(api_key), json=body, timeout=300.0,
            )
        except httpx.HTTPError as exc:
            raise SourceError("ai", f"{p.label}: {exc}") from exc

    resp = await post(payload)

    # Drop reasoning and retry if the model does not accept it, rather than
    # making the user work out which models can think.
    if resp.status_code >= 400 and "reasoning_effort" in payload:
        lowered = resp.text.lower()
        if "reasoning" in lowered or "unsupported" in lowered or resp.status_code == 400:
            payload.pop("reasoning_effort", None)
            resp = await post(payload)

    if resp.status_code == 401:
        raise SourceError("ai", f"{p.label} rejected the API key", 401)
    if resp.status_code == 429:
        raise SourceError("ai", f"{p.label} is rate limiting; wait a moment", 429)
    if resp.status_code >= 400:
        raise SourceError("ai", f"{p.label}: HTTP {resp.status_code} {resp.text[:240]}")

    body = resp.json()
    choices = body.get("choices") or []
    if not choices:
        raise SourceError("ai", f"{p.label} returned no completion")
    msg = choices[0].get("message") or {}

    calls = []
    for c in msg.get("tool_calls") or []:
        fn = c.get("function") or {}
        # Some providers hand back arguments already parsed; normalise to the
        # JSON string the rest of the loop and the transcript expect.
        arguments = fn.get("arguments")
        if isinstance(arguments, dict):
            arguments = json.dumps(arguments)
        calls.append(
            {
                "id": c.get("id") or f"call_{len(calls)}",
                "type": "function",
                "function": {"name": fn.get("name"), "arguments": arguments or "{}"},
            }
        )

    return {
        "content": msg.get("content") or "",
        "tool_calls": calls,
        # Some providers return the chain of thought separately; kept so the
        # UI can show that thinking happened without inventing it.
        "reasoning": msg.get("reasoning") or msg.get("reasoning_content") or "",
        "usage": body.get("usage") or {},
    }


async def complete_json(
    client: httpx.AsyncClient,
    provider: str,
    model: str,
    api_key: str,
    system: str,
    user: str,
    *,
    temperature: float = 0.0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """One completion, parsed as JSON. Returns (answer, usage).

    Temperature is zero: these tasks are extraction, not writing, and a
    different answer on each run would make the cached result meaningless.
    """
    p = PROVIDERS.get(provider)
    if not p:
        raise SourceError("ai", f"unknown provider '{provider}'")
    if not api_key:
        raise SourceError("ai", f"no API key stored for {p.label}")
    if not model:
        raise SourceError("ai", "no model chosen for this task")

    payload = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
    }

    try:
        resp = await client.post(
            f"{p.base_url}/chat/completions",
            headers=_headers(api_key), json=payload, timeout=120.0,
        )
    except httpx.HTTPError as exc:
        raise SourceError("ai", f"{p.label}: {exc}") from exc

    if resp.status_code == 401:
        raise SourceError("ai", f"{p.label} rejected the API key", 401)
    if resp.status_code >= 400:
        # Not every model supports a JSON response format; retry without it
        # rather than failing the task outright.
        if "response_format" in resp.text or resp.status_code == 400:
            payload.pop("response_format", None)
            payload["messages"][0]["content"] += "\n\nReply with JSON only, no prose."
            resp = await client.post(
                f"{p.base_url}/chat/completions",
                headers=_headers(api_key), json=payload, timeout=120.0,
            )
        if resp.status_code >= 400:
            raise SourceError("ai", f"{p.label}: HTTP {resp.status_code} {resp.text[:200]}")

    body = resp.json()
    choices = body.get("choices") or []
    if not choices:
        raise SourceError("ai", f"{p.label} returned no completion")
    text = (choices[0].get("message") or {}).get("content") or ""

    return parse_json(text), body.get("usage") or {}


def parse_json(text: str) -> dict[str, Any]:
    """Parse a model's reply, tolerating fenced code blocks and stray prose."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```", 2)[1] if cleaned.count("```") >= 2 else cleaned[3:]
        if cleaned.lstrip().lower().startswith("json"):
            cleaned = cleaned.lstrip()[4:]
    cleaned = cleaned.strip()

    try:
        return json.loads(cleaned)
    except ValueError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(cleaned[start : end + 1])
            except ValueError:
                pass
    raise SourceError("ai", f"model did not return JSON: {text[:160]}")
