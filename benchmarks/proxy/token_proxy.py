#!/usr/bin/env python3
"""Reverse proxy that intercepts Anthropic API calls and logs token usage.

Sits between Claude Code CLI and the Anthropic API, forwarding all requests
transparently while extracting token counts from streaming responses.

Usage:
    python -m benchmarks.proxy.token_proxy [--port 8082] [--output results/tokens.jsonl]
"""

import argparse
import json
import os
import time

from aiohttp import ClientSession, web


ANTHROPIC_API_BASE = "https://api.anthropic.com"
DEFAULT_PORT = 8082
DEFAULT_OUTPUT = os.path.join(os.path.dirname(__file__), "..", "results", "tokens.jsonl")
RUN_ID_FILE = os.path.join(os.path.dirname(__file__), "..", "results", ".current_run_id")

# Headers that should not be forwarded to the upstream API
HOP_BY_HOP_HEADERS = frozenset({
    "host", "transfer-encoding", "connection", "keep-alive",
    "proxy-authenticate", "proxy-authorization", "te", "trailers", "upgrade",
})


def _read_run_id() -> str:
    try:
        with open(RUN_ID_FILE) as f:
            return f.read().strip()
    except FileNotFoundError:
        return "unknown"


def _write_jsonl(path: str, record: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


async def _proxy_handler(request: web.Request) -> web.StreamResponse:
    """Forward request to Anthropic API, intercept streaming response for token data."""
    app = request.app
    output_path: str = app["output_path"]
    session: ClientSession = app["session"]

    # Build upstream URL
    upstream_url = ANTHROPIC_API_BASE + request.path
    if request.query_string:
        upstream_url += "?" + request.query_string

    # Forward headers (skip hop-by-hop)
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in HOP_BY_HOP_HEADERS
    }

    body = await request.read()

    # Parse request body for model info
    model = "unknown"
    try:
        req_json = json.loads(body)
        model = req_json.get("model", "unknown")
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass

    is_streaming = headers.get("Accept", "").startswith("text/event-stream") or (
        b'"stream":true' in body or b'"stream": true' in body
    )

    async with session.request(
        request.method,
        upstream_url,
        headers=headers,
        data=body,
        ssl=True,
    ) as upstream_resp:

        if is_streaming:
            return await _handle_streaming(
                request, upstream_resp, output_path, model
            )
        else:
            return await _handle_non_streaming(
                request, upstream_resp, output_path, model
            )


async def _handle_streaming(
    request: web.Request,
    upstream_resp,
    output_path: str,
    model: str,
) -> web.StreamResponse:
    """Handle SSE streaming response — forward chunks while extracting usage."""
    response = web.StreamResponse(
        status=upstream_resp.status,
        headers={
            k: v for k, v in upstream_resp.headers.items()
            if k.lower() not in HOP_BY_HOP_HEADERS
        },
    )
    await response.prepare(request)

    usage_data = {}
    buffer = b""

    async for chunk in upstream_resp.content.iter_any():
        # Forward immediately
        await response.write(chunk)

        # Parse SSE events to extract usage from message_delta and message_stop
        buffer += chunk
        while b"\n\n" in buffer:
            event_block, buffer = buffer.split(b"\n\n", 1)
            for line in event_block.split(b"\n"):
                line_str = line.decode("utf-8", errors="replace")
                if line_str.startswith("data: "):
                    data_str = line_str[6:]
                    if data_str.strip() == "[DONE]":
                        continue
                    try:
                        event_data = json.loads(data_str)
                        # message_start has initial usage
                        if event_data.get("type") == "message_start":
                            msg = event_data.get("message", {})
                            u = msg.get("usage", {})
                            if u:
                                usage_data["input_tokens"] = u.get("input_tokens", 0)
                                usage_data["cache_read_input_tokens"] = u.get(
                                    "cache_read_input_tokens", 0
                                )
                                usage_data["cache_creation_input_tokens"] = u.get(
                                    "cache_creation_input_tokens", 0
                                )
                        # message_delta has output usage
                        if event_data.get("type") == "message_delta":
                            u = event_data.get("usage", {})
                            if u:
                                usage_data["output_tokens"] = u.get("output_tokens", 0)
                    except json.JSONDecodeError:
                        pass

    # Flush remaining buffer
    if buffer:
        await response.write(buffer)

    await response.write_eof()

    # Log usage
    if usage_data:
        record = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "run_id": _read_run_id(),
            "model": model,
            **usage_data,
        }
        _write_jsonl(output_path, record)

    return response


async def _handle_non_streaming(
    request: web.Request,
    upstream_resp,
    output_path: str,
    model: str,
) -> web.Response:
    """Handle non-streaming response — read full body and extract usage."""
    body = await upstream_resp.read()

    usage_data = {}
    try:
        resp_json = json.loads(body)
        u = resp_json.get("usage", {})
        if u:
            usage_data = {
                "input_tokens": u.get("input_tokens", 0),
                "output_tokens": u.get("output_tokens", 0),
                "cache_read_input_tokens": u.get("cache_read_input_tokens", 0),
                "cache_creation_input_tokens": u.get("cache_creation_input_tokens", 0),
            }
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass

    if usage_data:
        record = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "run_id": _read_run_id(),
            "model": model,
            **usage_data,
        }
        _write_jsonl(output_path, record)

    return web.Response(
        status=upstream_resp.status,
        headers={
            k: v for k, v in upstream_resp.headers.items()
            if k.lower() not in HOP_BY_HOP_HEADERS
        },
        body=body,
    )


async def _on_startup(app: web.Application):
    app["session"] = ClientSession()


async def _on_cleanup(app: web.Application):
    await app["session"].close()


def create_app(output_path: str | None = None) -> web.Application:
    """Create the proxy application."""
    app = web.Application()
    app["output_path"] = output_path or os.path.abspath(DEFAULT_OUTPUT)
    app.router.add_route("*", "/{path_info:.*}", _proxy_handler)
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    return app


def main():
    parser = argparse.ArgumentParser(description="Anthropic API token-counting proxy")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port to listen on")
    parser.add_argument("--output", default=None, help="JSONL output file path")
    args = parser.parse_args()

    app = create_app(args.output)
    print(f"Token proxy starting on http://localhost:{args.port}")
    print(f"Logging to: {app['output_path']}")
    web.run_app(app, host="localhost", port=args.port, print=None)


if __name__ == "__main__":
    main()
