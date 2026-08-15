"""
title: OpenCode Agent Pipe
author: BRIGER
version: 2.0.0
license: MIT

description:
    Open WebUI Pipe integration for the BRIGER OpenCode server.

requirements:
    httpx
"""

import json
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional, List, Dict, Any

import httpx
from pydantic import BaseModel, Field
from fastapi import Request


class Pipe:

    class Valves(BaseModel):

        OPENCODE_SERVER_URL: str = Field(
            default="http://127.0.0.1:4096",
            description="OpenCode server URL",
        )

        OPENCODE_SERVER_USERNAME: str = Field(
            default="opencode",
            description="OpenCode username",
        )

        OPENCODE_SERVER_PASSWORD: str = Field(
            default="",
            description="OpenCode password",
        )

        GODMODE_ENABLED: bool = Field(
            default=True,
            description="Enable GodMode workflow",
        )

        GODMODE_AUTO_CHECKPOINT: bool = Field(
            default=True,
            description="Enable automatic checkpoints",
        )

        FALLBACK_MODEL: str = Field(
            default="",
            description="Fallback Open WebUI model",
        )

        REQUEST_TIMEOUT: float = Field(
            default=1800.0,
            description="OpenCode request timeout",
        )

        STREAMING_ENABLED: bool = Field(
            default=True,
            description="Enable streaming",
        )

        WORKSPACE_DIR: str = Field(
            default="/app/workspace",
            description="OpenCode workspace",
        )

    def __init__(self):

        self.type = "manifold"
        self.id = "opencode"
        self.name = "opencode/"

        self.valves = self.Valves()

        self._client: Optional[
            httpx.AsyncClient
        ] = None

        self._server_available: Optional[
            bool
        ] = None


    # =========================================================================
    # HTTP Client
    # =========================================================================

    def _get_client(
        self,
    ) -> httpx.AsyncClient:

        if self._client is None:

            auth = None

            if self.valves.OPENCODE_SERVER_PASSWORD:

                auth = httpx.BasicAuth(
                    self.valves.OPENCODE_SERVER_USERNAME,
                    self.valves.OPENCODE_SERVER_PASSWORD,
                )

            self._client = httpx.AsyncClient(
                base_url=self.valves.OPENCODE_SERVER_URL,
                auth=auth,
                timeout=httpx.Timeout(
                    self.valves.REQUEST_TIMEOUT,
                    connect=10.0,
                ),
                follow_redirects=True,
            )

        return self._client


    # =========================================================================
    # Health
    # =========================================================================

    async def _check_server_health(
        self,
    ) -> bool:

        try:

            client = self._get_client()

            response = await client.get(
                "/health",
                timeout=5.0,
            )

            if response.status_code < 500:

                self._server_available = True

                return True

        except Exception:

            pass

        self._server_available = False

        return False


    # =========================================================================
    # Models
    # =========================================================================

    def pipes(
        self,
    ) -> List[Dict[str, Any]]:

        return [
            {
                "id": "opencode-agent",
                "name": "OpenCode Agent",
                "description": (
                    "BRIGER OpenCode Agent with "
                    "GodMode workflow"
                ),
            },
            {
                "id": "opencode-fast",
                "name": "OpenCode Fast",
                "description": (
                    "OpenCode coding agent without "
                    "GodMode gating"
                ),
            },
        ]


    # =========================================================================
    # Prompt Construction
    # =========================================================================

    def _build_simple_prompt(
        self,
        messages: List[Dict[str, Any]],
    ) -> str:

        conversation = []

        for message in messages:

            role = message.get(
                "role",
                "user",
            )

            content = message.get(
                "content",
                "",
            )

            if not content:
                continue

            conversation.append(
                f"{role.upper()}:\n{content}"
            )

        return f"""
You are BRIGER's OpenCode coding agent.

WORKSPACE:
{self.valves.WORKSPACE_DIR}

Rules:

- Work only inside the workspace.
- Inspect the repository before changing files.
- Make minimal changes.
- Run relevant tests.
- Do not expose secrets.
- Do not perform destructive system operations.

Conversation:

{chr(10).join(conversation)}
""".strip()


    def _build_godmode_prompt(
        self,
        messages: List[Dict[str, Any]],
        model_id: str,
    ) -> str:

        if (
            not self.valves.GODMODE_ENABLED
            or model_id == "opencode-fast"
        ):

            return self._build_simple_prompt(
                messages
            )

        user_message = ""

        for message in reversed(messages):

            if message.get("role") == "user":

                user_message = message.get(
                    "content",
                    "",
                )

                break

        now = (
            datetime.now(timezone.utc)
            .isoformat()
        )

        return f"""
You are BRIGER's GodMode Engineering Orchestrator.

CURRENT TIME:
{now}

WORKSPACE:
{self.valves.WORKSPACE_DIR}

You must follow this engineering workflow:

## Stage 01 — DEFINE

- Understand the request.
- Identify requirements.
- Identify ambiguity.
- Produce a definition.
- Ask for approval if the task requires staged approval.

## Stage 02 — PLAN

- Inspect the repository.
- Identify affected files.
- Produce an implementation plan.
- Identify risks and rollback strategy.

## Stage 03 — EXECUTE

- Implement approved changes.
- Run appropriate tests.
- Avoid unnecessary changes.
- Never expose credentials.

## Stage 04 — REVIEW

- Review the implementation.
- Check correctness.
- Check security.
- Check tests.
- Identify remaining issues.

## Stage 05 — SHIP

- Perform final verification.
- Summarize changes.
- Report test results.
- Do not push remotely unless explicitly requested.

## Safety

- Do not modify files outside the workspace.
- Do not expose secrets.
- Do not delete the repository.
- Do not use destructive system commands.
- Do not perform git push unless explicitly requested.

## User Request

{user_message}
""".strip()


    # =========================================================================
    # OpenCode API
    # =========================================================================

    async def _call_opencode_api(
        self,
        prompt: str,
        stream: bool = True,
    ) -> AsyncGenerator[str, None]:

        client = self._get_client()

        payload = {
            "prompt": prompt,
            "stream": stream,
            "workspace": self.valves.WORKSPACE_DIR,
            "mode": "headless",
        }

        try:

            if stream:

                async with client.stream(
                    "POST",
                    "/tui",
                    json=payload,
                    timeout=httpx.Timeout(
                        self.valves.REQUEST_TIMEOUT,
                        connect=10.0,
                    ),
                ) as response:

                    response.raise_for_status()

                    async for line in response.aiter_lines():

                        if not line:
                            continue

                        if line.startswith(
                            "data: "
                        ):

                            data = line[
                                6:
                            ]

                            try:

                                parsed = json.loads(
                                    data
                                )

                                content = parsed.get(
                                    "content",
                                    "",
                                )

                                if content:
                                    yield content

                            except json.JSONDecodeError:

                                yield data

                        elif line.startswith(
                            "event:"
                        ):

                            continue

                        else:

                            yield line

            else:

                response = await client.post(
                    "/tui",
                    json=payload,
                    timeout=httpx.Timeout(
                        self.valves.REQUEST_TIMEOUT,
                        connect=10.0,
                    ),
                )

                response.raise_for_status()

                data = response.json()

                content = data.get(
                    "content",
                    data.get(
                        "response",
                        data.get(
                            "text",
                            "",
                        ),
                    ),
                )

                if content:
                    yield content
                else:
                    yield json.dumps(
                        data,
                        indent=2,
                    )

        except httpx.ConnectError:

            yield (
                "**OpenCode Server Unavailable**\n\n"
                f"Cannot connect to "
                f"`{self.valves.OPENCODE_SERVER_URL}`."
            )

        except httpx.TimeoutException:

            yield (
                "**OpenCode Server Timeout**\n\n"
                "The coding task exceeded the configured "
                "request timeout."
            )

        except httpx.HTTPStatusError as exc:

            yield (
                f"**OpenCode Server Error "
                f"({exc.response.status_code})**\n\n"
                f"```text\n"
                f"{exc.response.text[:2000]}"
                f"\n```"
            )

        except Exception as exc:

            yield (
                f"**OpenCode Integration Error**\n\n"
                f"`{type(exc).__name__}: "
                f"{str(exc)[:1000]}`"
            )


    # =========================================================================
    # Pipe
    # =========================================================================

    async def pipe(
        self,
        body: dict,
        __user__: dict,
        __request__: Request,
        __event_emitter__=None,
    ) -> AsyncGenerator[str, None]:

        model_id = (
            body.get(
                "model",
                "",
            )
            .replace(
                "opencode.",
                "",
                1,
            )
        )

        messages = body.get(
            "messages",
            [],
        )

        stream = body.get(
            "stream",
            True,
        )

        if not self.valves.STREAMING_ENABLED:
            stream = False


        if __event_emitter__:

            await __event_emitter__(
                "status",
                {
                    "description": (
                        "Connecting to BRIGER OpenCode..."
                    ),
                    "done": False,
                },
            )


        available = (
            await self._check_server_health()
        )


        if not available:

            if __event_emitter__:

                await __event_emitter__(
                    "status",
                    {
                        "description": (
                            "OpenCode server unavailable."
                        ),
                        "done": True,
                    },
                )

            yield (
                "**BRIGER OpenCode is unavailable.**\n\n"
                "Check the OpenCode server at "
                f"`{self.valves.OPENCODE_SERVER_URL}`."
            )

            return


        if __event_emitter__:

            await __event_emitter__(
                "status",
                {
                    "description": (
                        f"OpenCode Agent "
                        f"({model_id}) is working..."
                    ),
                    "done": False,
                },
            )


        prompt = self._build_godmode_prompt(
            messages,
            model_id,
        )


        try:

            async for chunk in self._call_opencode_api(
                prompt,
                stream=stream,
            ):

                if chunk:
                    yield chunk

        finally:

            if __event_emitter__:

                await __event_emitter__(
                    "status",
                    {
                        "description": (
                            "OpenCode Agent completed."
                        ),
                        "done": True,
                    },
                )


    # =========================================================================
    # Shutdown
    # =========================================================================

    async def on_shutdown(
        self,
    ):

        if self._client:

            await self._client.aclose()

            self._client = None
