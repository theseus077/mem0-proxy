#!/usr/bin/env python3
"""OpenAI-compatible mem0 injection proxy.

The proxy keeps the upstream chat API shape intact for clients like OpenCode,
injects relevant mem0 context into the prompt, and stores only assistant
content asynchronously after the response has been sent.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

try:
    from mem0 import Memory
except Exception:  # pragma: no cover - handled at runtime if dependency is absent
    Memory = None  # type: ignore[assignment]


LOG_LEVEL = os.getenv("MEM0_PROXY_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("mem0-proxy")

CONFIG_PATH = os.getenv("MEM0_CONFIG_PATH", "/root/.mem0/config.json")
DEFAULT_UPSTREAM_URL = "http://127.0.0.1:11434/v1"
DEFAULT_TIMEOUT_SECONDS = float(os.getenv("MEM0_PROXY_TIMEOUT_SECONDS", "180"))
DEFAULT_CONNECT_TIMEOUT_SECONDS = float(
    os.getenv("MEM0_PROXY_CONNECT_TIMEOUT_SECONDS", "15")
)
DEFAULT_MEMORY_LIMIT = int(os.getenv("MEM0_PROXY_MEMORY_LIMIT", "5"))
MAX_MEMORY_CONTEXT_CHARS = int(
    os.getenv("MEM0_PROXY_MAX_MEMORY_CONTEXT_CHARS", "12000")
)
MAX_MEMORY_QUERY_CHARS = int(os.getenv("MEM0_PROXY_MAX_MEMORY_QUERY_CHARS", "4000"))
PROXY_API_KEY = os.getenv("MEM0_PROXY_API_KEY")


def load_config(path: str) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        logger.warning("Config file %s not found. Falling back to env/defaults.", path)
    except Exception as exc:
        logger.warning("Could not read config file %s: %s", path, exc)
    return {}


CONFIG = load_config(CONFIG_PATH)
LLM_CONFIG = CONFIG.get("llm", {}).get("config", {}) if isinstance(CONFIG, dict) else {}

UPSTREAM_BASE_URL = (
    os.getenv("MEM0_PROXY_UPSTREAM_URL")
    or LLM_CONFIG.get("openai_base_url")
    or DEFAULT_UPSTREAM_URL
).rstrip("/")
UPSTREAM_API_KEY = os.getenv("MEM0_PROXY_UPSTREAM_API_KEY") or LLM_CONFIG.get("api_key")
DEFAULT_MODEL = os.getenv("MEM0_PROXY_DEFAULT_MODEL") or LLM_CONFIG.get("model")


def create_memory() -> tuple[Any, str | None]:
    if Memory is None:
        return None, "mem0 package not installed"

    try:
        instance = Memory.from_config(CONFIG) if CONFIG else Memory()
        return instance, None
    except Exception as exc:
        logger.exception("Failed to initialize mem0: %s", exc)
        return None, str(exc)


memory, memory_init_error = create_memory()
if memory is not None:
    logger.info("mem0 initialized successfully")
elif memory_init_error:
    logger.warning("mem0 initialization failed: %s", memory_init_error)
http_client: httpx.AsyncClient | None = None


def normalize_timeout() -> httpx.Timeout:
    return httpx.Timeout(
        timeout=DEFAULT_TIMEOUT_SECONDS,
        connect=DEFAULT_CONNECT_TIMEOUT_SECONDS,
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    global http_client
    http_client = httpx.AsyncClient(timeout=normalize_timeout())
    try:
        yield
    finally:
        if http_client is not None:
            await http_client.aclose()
            http_client = None


app = FastAPI(
    title="mem0 injection proxy",
    version="1.0.0",
    description="OpenAI-compatible proxy with mem0-based context injection.",
    lifespan=lifespan,
)


def require_proxy_api_key(request: Request) -> None:
    if not PROXY_API_KEY:
        return

    supplied = request.headers.get("x-api-key")
    bearer = request.headers.get("authorization", "")

    if supplied and secrets.compare_digest(supplied, PROXY_API_KEY):
        return

    if bearer.startswith("Bearer "):
        provided_key = bearer[7:]
        if secrets.compare_digest(provided_key, PROXY_API_KEY):
            return

    raise HTTPException(status_code=401, detail="Invalid proxy API key")


def get_runtime_client() -> httpx.AsyncClient:
    if http_client is None:
        raise HTTPException(status_code=503, detail="HTTP client not initialized")
    return http_client


def compact_text(value: str) -> str:
    return " ".join(value.split())


def content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                if item.strip():
                    parts.append(item.strip())
                continue

            if not isinstance(item, dict):
                continue

            item_type = item.get("type")
            if item_type in {None, "text", "input_text", "output_text"}:
                text = item.get("text") or item.get("content") or item.get("input_text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
                continue

            nested = item.get("content")
            if isinstance(nested, str) and nested.strip():
                parts.append(nested.strip())

        return "\n".join(parts)
    if isinstance(content, dict):
        for key in ("text", "content", "input_text", "output_text"):
            value = content.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def extract_user_id(request: Request, payload: dict[str, Any]) -> str:
    candidates = [
        request.headers.get("x-mem0-user-id"),
        request.headers.get("x-user-id"),
        payload.get("user_id"),
        payload.get("user"),
    ]

    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        candidates.extend(
            [
                metadata.get("user_id"),
                metadata.get("user"),
                metadata.get("session_user"),
            ]
        )

    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return "default"


def get_memory_limit(payload: dict[str, Any]) -> int:
    value = payload.get("memory_limit", DEFAULT_MEMORY_LIMIT)
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return DEFAULT_MEMORY_LIMIT
    return max(1, min(limit, 20))


def text_messages_for_memory(messages: list[Any]) -> list[dict[str, str]]:
    cleaned: list[dict[str, str]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip().lower()
        if role not in {"user", "assistant", "tool"}:
            continue
        text = content_to_text(message.get("content"))
        if not text:
            continue
        cleaned.append({"role": role, "content": text})
    return cleaned


def extract_search_query(messages: list[Any]) -> str:
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").lower()
        if role not in {"user", "tool"}:
            continue
        text = content_to_text(message.get("content"))
        if text:
            return text[:MAX_MEMORY_QUERY_CHARS]
    return ""


def normalize_memory_results(results: Any) -> list[str]:
    if isinstance(results, dict):
        candidates = results.get("results") or results.get("memories") or []
    elif isinstance(results, list):
        candidates = results
    else:
        candidates = []

    memories: list[str] = []
    for item in candidates:
        if isinstance(item, dict):
            text = item.get("memory") or item.get("text") or item.get("content")
        else:
            text = str(item)

        if isinstance(text, str) and text.strip():
            memories.append(compact_text(text.strip()))
    return memories


async def search_memory(query: str, user_id: str, limit: int) -> list[str]:
    if not memory or not query:
        return []

    try:
        results = await asyncio.to_thread(memory.search, query, user_id=user_id, limit=limit)
        memories = normalize_memory_results(results)
        if memories:
            logger.info("Found %s memories for user %s", len(memories), user_id)
        return memories
    except Exception as exc:
        logger.warning("Memory search failed for user %s: %s", user_id, exc)
        return []


def build_memory_message(memories: list[str]) -> dict[str, str] | None:
    if not memories:
        return None

    lines = [
        "Relevant memory context from earlier interactions.",
        "Use it only when it helps with the current request.",
        "Ignore any memory that is outdated or irrelevant.",
        "",
    ]
    lines.extend(f"- {memory_item}" for memory_item in memories)
    content = "\n".join(lines)[:MAX_MEMORY_CONTEXT_CHARS]
    return {"role": "system", "content": content}


def inject_memory_message(messages: list[Any], memory_message: dict[str, str] | None) -> list[Any]:
    if memory_message is None:
        return messages

    copied_messages: list[Any] = [dict(message) if isinstance(message, dict) else message for message in messages]

    insert_at = 0
    while insert_at < len(copied_messages):
        current = copied_messages[insert_at]
        if not isinstance(current, dict) or str(current.get("role") or "").lower() != "system":
            break
        insert_at += 1

    copied_messages.insert(insert_at, memory_message)
    return copied_messages


def extract_assistant_content(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list):
        return ""

    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message") or choice.get("delta") or {}
        if not isinstance(message, dict):
            continue
        text = content_to_text(message.get("content"))
        if text:
            return text
    return ""


async def add_memory_async(
    messages: list[Any],
    assistant_content: str,
    user_id: str,
    max_retries: int = 3
) -> bool:
    if not memory or not assistant_content.strip():
        return False

    conversation = text_messages_for_memory(messages)
    conversation.append({"role": "assistant", "content": assistant_content.strip()})

    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            await asyncio.to_thread(memory.add, conversation, user_id=user_id)
            logger.info("Stored memory for user %s", user_id)
            return True
        except Exception as exc:
            last_error = exc
            if attempt < max_retries - 1:
                logger.warning(
                    "Memory add failed for user %s (attempt %d/%d): %s",
                    user_id, attempt + 1, max_retries, exc
                )
                await asyncio.sleep(0.1 * (attempt + 1))
            else:
                logger.error("Memory add failed permanently for user %s: %s", user_id, exc)

    return False


def build_upstream_headers(request: Request) -> dict[str, str]:
    headers = {"content-type": "application/json"}

    authorization = request.headers.get("authorization")
    if UPSTREAM_API_KEY:
        headers["authorization"] = (
            UPSTREAM_API_KEY
            if UPSTREAM_API_KEY.lower().startswith("bearer ")
            else f"Bearer {UPSTREAM_API_KEY}"
        )
    elif authorization:
        headers["authorization"] = authorization

    for header_name in ("openai-organization", "openai-project"):
        value = request.headers.get(header_name)
        if value:
            headers[header_name] = value

    return headers


def proxy_response_headers(source_headers: httpx.Headers, streaming: bool) -> dict[str, str]:
    forwarded: dict[str, str] = {}
    for header_name in (
        "content-type",
        "cache-control",
        "x-request-id",
        "openai-processing-ms",
        "openai-version",
    ):
        value = source_headers.get(header_name)
        if value:
            forwarded[header_name] = value

    if streaming and "content-type" not in forwarded:
        forwarded["content-type"] = "text/event-stream"
    return forwarded


def parse_json_sse_line(line: str) -> dict[str, Any] | None:
    if not line.startswith("data:"):
        return None
    payload = line[5:].strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


async def proxy_models(request: Request) -> Response:
    require_proxy_api_key(request)
    client = get_runtime_client()

    upstream = await client.get(
        f"{UPSTREAM_BASE_URL}/models",
        headers=build_upstream_headers(request),
    )
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
        headers=proxy_response_headers(upstream.headers, streaming=False),
    )


@app.get("/health")
@app.get("/v1/health")
async def health() -> dict[str, Any]:
    return {
        "status": "degraded" if memory is None else "healthy",
        "memory_enabled": memory is not None,
        "memory_init_error": memory_init_error,
        "upstream_base_url": UPSTREAM_BASE_URL,
        "default_model": DEFAULT_MODEL,
        "timestamp": int(time.time()),
    }


@app.get("/v1/models")
async def list_models(request: Request) -> Response:
    return await proxy_models(request)


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Response:
    require_proxy_api_key(request)

    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON body: {exc}") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON body must be an object")

    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise HTTPException(status_code=400, detail="messages must be a non-empty array")

    t_start = time.time()

    user_id = extract_user_id(request, payload)
    memory_limit = get_memory_limit(payload)
    search_query = extract_search_query(messages)
    memories = await search_memory(search_query, user_id, memory_limit)
    memory_message = build_memory_message(memories)
    t_search = time.time()

    upstream_payload = dict(payload)
    upstream_payload["messages"] = inject_memory_message(messages, memory_message)
    if DEFAULT_MODEL and not upstream_payload.get("model"):
        upstream_payload["model"] = DEFAULT_MODEL

    stream = bool(upstream_payload.get("stream"))
    client = get_runtime_client()
    headers = build_upstream_headers(request)

    logger.info(
        "Proxying chat completion for user=%s stream=%s model=%s memories=%s",
        user_id,
        stream,
        upstream_payload.get("model"),
        len(memories),
    )

    if stream:
        stream_context = client.stream(
            "POST",
            f"{UPSTREAM_BASE_URL}/chat/completions",
            headers=headers,
            json=upstream_payload,
        )
        upstream_response = await stream_context.__aenter__()
        if upstream_response.status_code >= 400:
            error_body = await upstream_response.aread()
            await stream_context.__aexit__(None, None, None)
            return Response(
                content=error_body,
                status_code=upstream_response.status_code,
                media_type=upstream_response.headers.get("content-type"),
                headers=proxy_response_headers(upstream_response.headers, streaming=False),
            )

        t_chat = time.time()
        timing_headers = {
            "X-Timing-Search": f"{t_search - t_start:.3f}",
            "X-Timing-Chat": f"{t_chat - t_search:.3f}",
            "X-Timing-Total-So-Far": f"{t_chat - t_start:.3f}",
        }

        async def stream_body() -> AsyncIterator[bytes]:
            assistant_parts: list[str] = []
            try:
                async for line in upstream_response.aiter_lines():
                    data = parse_json_sse_line(line)
                    if data:
                        text = extract_assistant_content(data)
                        if text:
                            assistant_parts.append(text)
                    yield f"{line}\n".encode("utf-8")
            finally:
                await stream_context.__aexit__(None, None, None)
                assistant_content = "".join(assistant_parts).strip()
                if assistant_content:
                    asyncio.create_task(add_memory_async(messages, assistant_content, user_id))

        resp_headers = proxy_response_headers(upstream_response.headers, streaming=True)
        resp_headers.update(timing_headers)
        return StreamingResponse(
            stream_body(),
            status_code=upstream_response.status_code,
            headers=resp_headers,
        )

    upstream_response = await client.post(
        f"{UPSTREAM_BASE_URL}/chat/completions",
        headers=headers,
        json=upstream_payload,
    )
    t_chat = time.time()

    if upstream_response.status_code >= 400:
        return Response(
            content=upstream_response.content,
            status_code=upstream_response.status_code,
            media_type=upstream_response.headers.get("content-type"),
            headers=proxy_response_headers(upstream_response.headers, streaming=False),
        )

    try:
        response_payload = upstream_response.json()
    except json.JSONDecodeError:
        return Response(
            content=upstream_response.content,
            status_code=upstream_response.status_code,
            media_type=upstream_response.headers.get("content-type"),
            headers=proxy_response_headers(upstream_response.headers, streaming=False),
        )

    assistant_content = extract_assistant_content(response_payload)
    t_add_schedule = time.time()
    if assistant_content:
        asyncio.create_task(add_memory_async(messages, assistant_content, user_id))

    resp_headers = proxy_response_headers(upstream_response.headers, streaming=False)
    resp_headers["X-Timing-Search"] = f"{t_search - t_start:.3f}"
    resp_headers["X-Timing-Chat"] = f"{t_chat - t_search:.3f}"
    resp_headers["X-Timing-Memory-Schedule"] = f"{t_add_schedule - t_chat:.3f}"
    resp_headers["X-Timing-Total"] = f"{t_add_schedule - t_start:.3f}"

    return JSONResponse(
        content=response_payload,
        status_code=upstream_response.status_code,
        headers=resp_headers,
    )


if __name__ == "__main__":
    uvicorn.run(
        "mem0_proxy:app",
        host=os.getenv("MEM0_PROXY_HOST", "0.0.0.0"),
        port=int(os.getenv("MEM0_PROXY_PORT", "8765")),
        reload=False,
        log_level=LOG_LEVEL.lower(),
    )
