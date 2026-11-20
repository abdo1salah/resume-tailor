from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .config import load_config, provider_info


class LLMError(RuntimeError):
    pass


def _client(timeout: float = 240.0) -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(timeout, connect=20.0))


def _headers(provider: str) -> tuple[dict[str, str], str]:
    info = provider_info(provider)
    if info["api_key_env"] and not info["api_key"]:
        raise LLMError(
            f"{info['label']} needs an API key. Set {info['api_key_env']} in .env "
            "or switch the provider to Ollama."
        )
    headers = {"Content-Type": "application/json"}
    if info["api_key"]:
        headers["Authorization"] = f"Bearer {info['api_key']}"
    return headers, info["base_url"]


def list_models(provider: str, cfg: dict[str, Any] | None = None) -> list[str]:
    cfg = cfg or load_config()
    info = provider_info(provider, cfg)
    headers = {"Content-Type": "application/json"}
    if info["api_key_env"] and not info["api_key"]:
        raise LLMError(
            f"{info['label']} is not configured. Set {info['api_key_env']} in .env."
        )
    if info["api_key"]:
        headers["Authorization"] = f"Bearer {info['api_key']}"
    try:
        with _client(30.0) as client:
            response = client.get(f"{info['base_url']}/models", headers=headers)
    except httpx.RequestError as exc:
        raise LLMError(f"Could not reach {info['label']} at {info['base_url']}: {exc}") from exc
    if response.status_code >= 400:
        raise LLMError(
            f"{info['label']} returned HTTP {response.status_code} when listing models."
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise LLMError(f"{info['label']} returned an invalid model list.") from exc

    raw_items: list[Any]
    if isinstance(data, list):
        raw_items = data
    else:
        raw_items = data.get("data") or data.get("models") or []
    models: set[str] = set()
    for item in raw_items:
        if isinstance(item, dict):
            name = item.get("id") or item.get("name") or item.get("model")
        else:
            name = str(item)
        if name:
            models.add(str(name))
    return sorted(models, key=str.lower)


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in the model response")
    candidate = cleaned[start : end + 1]
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
        parsed = json.loads(candidate)
    if not isinstance(parsed, dict):
        raise ValueError("The model response was not a JSON object")
    return parsed


def _ollama_chat(
    info: dict[str, str],
    model: str,
    messages: list[dict[str, str]],
    *,
    json_mode: bool,
    temperature: float,
    max_tokens: int,
) -> str:
    base = info["base_url"].rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    url = f"{base}/api/chat"
    base_payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    }
    if json_mode:
        base_payload["format"] = "json"

    attempts = [base_payload]
    without_think = dict(base_payload)
    without_think.pop("think", None)
    attempts.append(without_think)
    without_format = dict(without_think)
    without_format.pop("format", None)
    attempts.append(without_format)

    last_error = ""
    for payload in attempts:
        try:
            with _client() as client:
                response = client.post(url, headers={"Content-Type": "application/json"}, json=payload)
        except httpx.RequestError as exc:
            raise LLMError(f"Could not reach Ollama at {base}: {exc}") from exc
        if response.status_code < 400:
            try:
                data = response.json()
            except ValueError as exc:
                raise LLMError("Ollama returned a non-JSON response.") from exc
            content = ((data.get("message") or {}).get("content") or "").strip()
            if content:
                return content
            last_error = "Ollama returned an empty response."
            continue
        last_error = response.text[:400].replace("\n", " ")
    raise LLMError(f"Ollama error: {last_error}")


def chat(
    provider: str,
    model: str,
    messages: list[dict[str, str]],
    *,
    cfg: dict[str, Any] | None = None,
    json_mode: bool = True,
    temperature: float = 0.15,
    max_tokens: int = 4096,
) -> str:
    cfg = cfg or load_config()
    info = provider_info(provider, cfg)
    model = model or info["model"]
    if not model:
        raise LLMError(f"No model selected for {info['label']}.")
    if provider == "ollama":
        return _ollama_chat(
            info,
            model,
            messages,
            json_mode=json_mode,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    headers = {"Content-Type": "application/json"}
    if info["api_key_env"] and not info["api_key"]:
        raise LLMError(
            f"{info['label']} needs an API key. Set {info['api_key_env']} in .env "
            "or switch the provider to Ollama."
        )
    if info["api_key"]:
        headers["Authorization"] = f"Bearer {info['api_key']}"

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    url = f"{info['base_url']}/chat/completions"
    try:
        with _client() as client:
            response = client.post(url, headers=headers, json=payload)
            if response.status_code in (400, 404, 422) and json_mode:
                payload.pop("response_format", None)
                response = client.post(url, headers=headers, json=payload)
    except httpx.RequestError as exc:
        raise LLMError(f"Could not reach {info['label']} at {info['base_url']}: {exc}") from exc

    if response.status_code >= 400:
        detail = response.text[:400].replace("\n", " ")
        raise LLMError(
            f"{info['label']} returned HTTP {response.status_code}: {detail}"
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise LLMError(f"{info['label']} returned a non-JSON response.") from exc

    try:
        message = data["choices"][0]["message"]
        content = message.get("content") or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"Unexpected response shape from {info['label']}.") from exc
    if not content.strip():
        raise LLMError(f"{info['label']} returned an empty response.")
    return content


def chat_json(
    provider: str,
    model: str,
    messages: list[dict[str, str]],
    *,
    cfg: dict[str, Any] | None = None,
    temperature: float = 0.15,
    max_tokens: int = 4096,
) -> dict[str, Any]:
    content = chat(
        provider,
        model,
        messages,
        cfg=cfg,
        json_mode=True,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    try:
        return _extract_json(content)
    except (json.JSONDecodeError, ValueError) as first_error:
        repair_messages = messages + [
            {"role": "assistant", "content": content},
            {
                "role": "user",
                "content": (
                    "Your previous response could not be parsed as JSON "
                    f"({first_error}). Return ONLY one valid JSON object matching "
                    "the requested schema. No markdown, no commentary."
                ),
            },
        ]
        repaired = chat(
            provider,
            model,
            repair_messages,
            cfg=cfg,
            json_mode=True,
            temperature=0.0,
            max_tokens=max_tokens,
        )
        try:
            return _extract_json(repaired)
        except (json.JSONDecodeError, ValueError) as second_error:
            raise LLMError(
                f"The model did not return valid JSON: {second_error}"
            ) from second_error
