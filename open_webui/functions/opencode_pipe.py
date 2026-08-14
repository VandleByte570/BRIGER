"""
title: OpenCode Agent Pipe
author: Unified AI Suite
version: 1.0.0
license: MIT
description: >
  Integrates OpenCode headless server into Open WebUI as a first-class model.
  This Pipe Function proxies chat requests to the local OpenCode server
  (running on port 4096 inside the container) and enforces the GodMode
  5-stage gated engineering workflow.

  Features:
    - Auto-discovery of local OpenCode server
    - GodMode workflow enforcement (Define -> Plan -> Execute -> Review -> Ship)
    - Streaming response support
    - Graceful fallback to direct LLM if OpenCode is unavailable
    - Structured execution logging

requirements: httpx
"""

import os
import json
import asyncio
from typing import AsyncGenerator, Optional, List, Dict, Any
from datetime import datetime

from pydantic import BaseModel, Field
from fastapi import Request
import httpx


class Pipe:
    """
    OpenCode Agent Pipe for Open WebUI.

    This pipe registers as a model named "OpenCode Agent" in the Open WebUI
    model selector. When selected, user messages are forwarded to the local
    OpenCode server for agentic coding execution with GodMode workflow
    enforcement.
    """

    class Valves(BaseModel):
        """Configurable valves for the pipe."""
        OPENCODE_SERVER_URL: str = Field(
            default="http://127.0.0.1:4096",
            description="OpenCode server base URL"
        )
        OPENCODE_SERVER_USERNAME: str = Field(
            default="opencode",
            description="OpenCode server basic auth username"
        )
        OPENCODE_SERVER_PASSWORD: str = Field(
            default="",
            description="OpenCode server basic auth password"
        )
        GODMODE_ENABLED: bool = Field(
            default=True,
            description="Enable GodMode 5-stage workflow enforcement"
        )
        GODMODE_AUTO_CHECKPOINT: bool = Field(
            default=True,
            description="Auto-save checkpoints between workflow stages"
        )
        FALLBACK_MODEL: str = Field(
            default="",
            description="Fallback model ID if OpenCode server is unavailable"
        )
        REQUEST_TIMEOUT: float = Field(
            default=300.0,
            description="HTTP request timeout in seconds"
        )
        STREAMING_ENABLED: bool = Field(
            default=True,
            description="Enable streaming responses"
        )
        WORKSPACE_DIR: str = Field(
            default="/app/workspace",
            description="OpenCode workspace directory"
        )

    def __init__(self):
        self.type = "manifold"
        self.id = "opencode"
        self.name = "opencode/"
        self.valves = self.Valves()
        self._client: Optional[httpx.AsyncClient] = None
        self._server_available: Optional[bool] = None

    def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client with auth."""
        if self._client is None:
            auth = None
            if self.valves.OPENCODE_SERVER_PASSWORD:
                auth = httpx.BasicAuth(
                    self.valves.OPENCODE_SERVER_USERNAME,
                    self.valves.OPENCODE_SERVER_PASSWORD
                )
            self._client = httpx.AsyncClient(
                base_url=self.valves.OPENCODE_SERVER_URL,
                auth=auth,
                timeout=self.valves.REQUEST_TIMEOUT,
                follow_redirects=True
            )
        return self._client

    async def _check_server_health(self) -> bool:
        """Check if OpenCode server is reachable."""
        if self._server_available is not None:
            return self._server_available
        try:
            client = self._get_client()
            for endpoint in ["/health", "/docs", "/"]:
                try:
                    resp = await client.get(endpoint, timeout=5.0)
                    if resp.status_code < 500:
                        self._server_available = True
                        return True
                except Exception:
                    continue
            self._server_available = False
        except Exception:
            self._server_available = False
        return self._server_available

    def pipes(self) -> List[Dict[str, Any]]:
        """Return available models (manifold)."""
        return [
            {
                "id": "opencode-agent",
                "name": "OpenCode Agent",
                "description": "Agentic coding with GodMode workflow (Define->Plan->Execute->Review->Ship)"
            },
            {
                "id": "opencode-fast",
                "name": "OpenCode Fast",
                "description": "Quick coding tasks without full GodMode gating"
            }
        ]

    def _build_godmode_prompt(self, messages: List[Dict[str, Any]], model_id: str) -> str:
        """Inject GodMode workflow instructions into the prompt."""
        if not self.valves.GODMODE_ENABLED or model_id == "opencode-fast":
            return self._build_simple_prompt(messages)

        user_message = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_message = msg.get("content", "")
                break

        godmode_system = f"""You are the GodMode Engineering Orchestrator.

CURRENT TIME: {datetime.utcnow().isoformat()}Z
WORKSPACE: {self.valves.WORKSPACE_DIR}

## GodMode Workflow Enforcement
You MUST follow the 5-stage gated engineering workflow:

### Stage 01: DEFINE
- Extract clear requirements from the user's request
- Ask clarifying questions if anything is ambiguous
- Produce a written definition document
- WAIT for user approval before proceeding

### Stage 02: PLAN
- Analyze the codebase context
- Break work into atomic, sequenced, verifiable tasks
- Document architecture decisions
- Include rollback strategy
- WAIT for user approval before proceeding

### Stage 03: EXECUTE
- Implement tasks in order with checkpoint commits
- Ask for confirmation before destructive operations
- Run tests and verification after each task
- Maintain execution log

### Stage 04: REVIEW
- Self-review against requirements, quality, security, performance
- Produce review report
- WAIT for user approval before shipping

### Stage 05: SHIP
- Final verification
- Merge and deliver
- Produce ship report

## Safety Rules
- ALWAYS ask before rm, git push, or overwriting files
- NEVER execute commands without understanding them
- ALWAYS run tests after code changes
- NEVER skip workflow stages
- ALWAYS checkpoint with git commits between stages

## User Request
{user_message}

Begin with Stage 01: DEFINE. Present your definition document and ask for approval.
"""
        return godmode_system

    def _build_simple_prompt(self, messages: List[Dict[str, Any]]) -> str:
        """Build a simple prompt for fast mode."""
        user_message = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_message = msg.get("content", "")
                break

        return f"""You are an expert software engineering assistant running inside the OpenCode environment.

WORKSPACE: {self.valves.WORKSPACE_DIR}

## Capabilities
- Read and write files
- Execute shell commands
- Run git operations
- Analyze code with LSP
- Manage git worktrees

## Safety
- Ask for confirmation before destructive operations
- Never commit secrets or credentials
- Run tests when available

## User Request
{user_message}

Provide a helpful, accurate response. If the task involves file changes, show the changes clearly.
"""

    async def _call_opencode_api(self, prompt: str, stream: bool = False) -> AsyncGenerator[str, None]:
        """Call OpenCode server API with the given prompt."""
        client = self._get_client()

        payload = {
            "prompt": prompt,
            "stream": stream,
            "workspace": self.valves.WORKSPACE_DIR,
            "mode": "headless"
        }

        try:
            if stream:
                async with client.stream(
                    "POST",
                    "/tui",
                    json=payload,
                    timeout=self.valves.REQUEST_TIMEOUT
                ) as response:
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data = line[6:]
                            try:
                                parsed = json.loads(data)
                                content = parsed.get("content", "")
                                if content:
                                    yield content
                            except json.JSONDecodeError:
                                yield data
                        elif line.strip():
                            yield line
            else:
                response = await client.post(
                    "/tui",
                    json=payload,
                    timeout=self.valves.REQUEST_TIMEOUT
                )
                response.raise_for_status()
                data = response.json()
                content = data.get("content", data.get("response", data.get("text", str(data))))
                yield content

        except httpx.ConnectError:
            yield "**OpenCode Server Unavailable**\n\n"
            yield "The OpenCode server at `{}` is not reachable. ".format(self.valves.OPENCODE_SERVER_URL)
            yield "Please check that the server is running on port 4096.\n\n"
            yield "You can still use Open WebUI with other configured models."
        except httpx.HTTPStatusError as e:
            yield f"**OpenCode Server Error** ({e.response.status_code})\n\n"
            yield f"```\n{e.response.text[:500]}\n```"
        except Exception as e:
            yield f"**Integration Error**: {type(e).__name__}: {str(e)[:500]}"

    async def _fallback_to_llm(
        self,
        body: dict,
        __request__: Request,
        __user__: dict
    ) -> AsyncGenerator[str, None]:
        """Fallback to Open WebUI's internal LLM routing."""
        try:
            from open_webui.utils.chat import generate_chat_completion
            from open_webui.models.users import Users

            user = await Users.get_user_by_id(__user__["id"])
            fallback_model = self.valves.FALLBACK_MODEL or body.get("model", "")
            if fallback_model.startswith("opencode."):
                fallback_model = fallback_model.replace("opencode.", "", 1)
            body["model"] = fallback_model or "gpt-4o"

            result = await generate_chat_completion(__request__, body, user)
            if hasattr(result, "__aiter__"):
                async for chunk in result:
                    yield chunk
            else:
                yield result
        except Exception as e:
            yield f"**Fallback Error**: {type(e).__name__}: {str(e)[:500]}"

    async def pipe(
        self,
        body: dict,
        __user__: dict,
        __request__: Request,
        __event_emitter__=None
    ) -> AsyncGenerator[str, None]:
        """
        Main pipe handler. Receives Open WebUI chat completion requests
        and routes them to OpenCode server.
        """
        model_id = body.get("model", "").replace("opencode.", "", 1)
        messages = body.get("messages", [])
        stream = body.get("stream", True)

        if __event_emitter__:
            await __event_emitter__(
                "status",
                {"description": "Connecting to OpenCode server...", "done": False}
            )

        is_available = await self._check_server_health()

        if not is_available:
            if __event_emitter__:
                await
