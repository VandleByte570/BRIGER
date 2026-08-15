"""
title: BRIGER OpenCode Pipe
author: BRIGER
version: 2.1.0
license: MIT

Open WebUI Pipe for the BRIGER OpenCode server.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx
from fastapi import Request
from pydantic import BaseModel, Field


class Pipe:

    class Valves(BaseModel):

        OPENCODE_SERVER_URL: str = Field(
            default="http://127.0.0.1:4096",
            description="BRIGER OpenCode server URL",
        )

        OPENCODE_SERVER_USERNAME: str = Field(
            default="opencode",
            description="OpenCode server username",
        )

        OPENCODE_SERVER_PASSWORD: str = Field(
            default="",
            description="OpenCode server password",
        )

        WORKSPACE_DIR: str = Field(
            default="/app/workspace",
            description="BRIGER workspace",
        )

        REQUEST_TIMEOUT: float = Field(
            default=1800.0,
            description="Maximum request time in seconds",
        )

        CONNECT_TIMEOUT: float = Field(
            default=10.0,
            description="Connection timeout",
        )

        STREAMING_ENABLED: bool = Field(
            default=True,
            description="Enable streaming responses",
        )

        GODMODE_ENABLED: bool = Field(
            default=True,
            description="Enable BRIGER GodMode instructions",
        )

    def __init__(self):

        self.type = "manifold"

        self.id = "opencode"

        self.name = "opencode/"

        self.valves = self.Valves()

        self._client: Optional[
            httpx.AsyncClient
        ] = None

    # =========================================================================
    # HTTP CLIENT
    # =========================================================================

    def _get_client(self) -> httpx.AsyncClient:

        if self._client is None:

            timeout = httpx.Timeout(
                timeout=self.valves.REQUEST_TIMEOUT,
                connect=self.valves.CONNECT_TIMEOUT,
            )

            auth = None

            if self.valves.OPENCODE_SERVER_PASSWORD:

                auth = httpx.BasicAuth(
                    self.valves.OPENCODE_SERVER_USERNAME,
                    self.valves.OPENCODE_SERVER_PASSWORD,
                )

            self._client = httpx.AsyncClient(
                base_url=self.valves.OPENCODE_SERVER_URL.rstrip(
                    "/"
                ),
                timeout=timeout,
                auth=auth,
                follow_redirects=True,
            )

        return self._client

    # =========================================================================
    # PIPE LIST
    # =========================================================================

    def pipes(
        self,
    ) -> List[Dict[str, Any]]:

        return [
            {
                "id": "opencode-agent",
                "name": "OpenCode Agent",
                "description": (
                    "BRIGER OpenCode coding agent "
                    "with GodMode workflow."
                ),
            },
            {
                "id": "opencode-fast",
                "name": "OpenCode Fast",
                "description": (
                    "BRIGER OpenCode coding agent "
                    "without extended GodMode instructions."
                ),
            },
        ]

    # =========================================================================
    # HEALTH CHECK
    # =========================================================================

    async def _check_health(self) -> bool:

        try:

            client = self._get_client()

            response = await client.get(
                "/health",
                timeout=httpx.Timeout(
                    5.0,
                    connect=3.0,
                ),
            )

            return response.is_success

        except Exception:

            return False

    # =========================================================================
    # MESSAGE HELPERS
    # =========================================================================

    @staticmethod
    def _content_to_text(
        content: Any,
    ) -> str:

        if isinstance(
            content,
            str,
        ):
            return content

        if isinstance(
            content,
            list,
        ):

            parts = []

            for item in content:

                if isinstance(
                    item,
                    str,
                ):

                    parts.append(item)

                elif isinstance(
                    item,
                    dict,
                ):

                    text = item.get(
                        "text"
                    )

                    if text:
                        parts.append(
                            str(text)
                        )

            return "\n".join(parts)

        if content is None:
            return ""

        return str(content)

    def _conversation_text(
        self,
        messages: List[Dict[str, Any]],
    ) -> str:

        parts = []

        for message in messages:

            role = str(
                message.get(
                    "role",
                    "user",
                )
            ).upper()

            content = self._content_to_text(
                message.get(
                    "content",
                    "",
                )
            ).strip()

            if not content:
                continue

            parts.append(
                f"{role}:\n{content}"
            )

        return "\n\n".join(parts)

    # =========================================================================
    # GODMODE PROMPT
    # =========================================================================

    def _build_prompt(
        self,
        messages: List[Dict[str, Any]],
        model_id: str,
    ) -> str:

        conversation = self._conversation_text(
            messages
        )

        if not self.valves.GODMODE_ENABLED:

            return f"""
You are BRIGER's OpenCode coding agent.

WORKSPACE:
{self.valves.WORKSPACE_DIR}

Rules:

- Work only inside the workspace.
- Inspect the repository before editing.
- Make minimal changes.
- Run relevant tests.
- Never expose secrets.
- Never push to a remote repository unless explicitly requested.

USER CONVERSATION:

{conversation}
""".strip()

        return f"""
You are BRIGER's OpenCode engineering agent.

WORKSPACE:
{self.valves.WORKSPACE_DIR}

CURRENT UTC TIME:
{datetime.now(timezone.utc).isoformat()}

You must work as a careful software engineer.

## DEFINE

Understand the user's actual request.

Identify:

- requirements
- constraints
- affected components
- possible risks

## PLAN

Before making changes:

- inspect the repository
- inspect relevant files
- understand existing architecture
- identify the smallest safe change

Do not blindly rewrite unrelated files.

## EXECUTE

Implement the requested change.

Rules:

- work only inside the workspace
- preserve existing functionality
- avoid unnecessary dependencies
- do not expose secrets
- do not modify the host system
- do not delete the repository
- do not push to Git remotes unless explicitly requested

## VERIFY

After modifying files:

- run relevant tests
- run syntax checks where appropriate
- inspect changed files
- fix errors you introduced

## REPORT

At the end, clearly report:

1. files changed
2. what was fixed
3. tests/checks performed
4. remaining problems, if any

USER CONVERSATION:

{conversation}
""".strip()

    # =========================================================================
    # RESPONSE PARSING
    # =========================================================================

    @staticmethod
    def _extract_text(
        payload: Any,
    ) -> str:

        if isinstance(
            payload,
            str,
        ):
            return payload

        if not isinstance(
            payload,
            dict,
        ):
            return str(payload)

        for key in (
            "content",
            "response",
            "text",
            "message",
            "output",
        ):

            value = payload.get(
                key
            )

            if isinstance(
                value,
                str,
            ) and value:

                return value

        return ""

    # =========================================================================
    # NON-STREAM REQUEST
    # =========================================================================

    async def _request_once(
        self,
        prompt: str,
    ) -> str:

        client = self._get_client()

        response = await client.post(
            "/tui",
            json={
                "prompt": prompt,
                "stream": False,
                "workspace": self.valves.WORKSPACE_DIR,
                "mode": "headless",
            },
        )

        response.raise_for_status()

        try:

            payload = response.json()

        except json.JSONDecodeError:

            return response.text

        text = self._extract_text(
            payload
        )

        if text:
            return text

        return json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
        )

    # =========================================================================
    # STREAM REQUEST
    # =========================================================================

    async def _request_stream(
        self,
        prompt: str,
    ) -> AsyncGenerator[str, None]:

        client = self._get_client()

        async with client.stream(
            "POST",
            "/tui",
            json={
                "prompt": prompt,
                "stream": True,
                "workspace": self.valves.WORKSPACE_DIR,
                "mode": "headless",
            },
        ) as response:

            response.raise_for_status()

            async for line in response.aiter_lines():

                if not line:
                    continue

                if line.startswith(
                    "data:"
                ):

                    raw = line[
                        len("data:")
                    ].strip()

                    if not raw:
                        continue

                    try:

                        payload = json.loads(
                            raw
                        )

                    except json.JSONDecodeError:

                        yield raw

                        continue

                    if payload.get(
                        "done"
                    ):

                        continue

                    if payload.get(
                        "error"
                    ):

                        yield (
                            "\n\n**OpenCode error:** "
                            + str(
                                payload["error"]
                            )
                        )

                        continue

                    text = self._extract_text(
                        payload
                    )

                    if text:
                        yield text

                else:

                    yield line

    # =========================================================================
    # PIPE
    # =========================================================================

    async def pipe(
        self,
        body: dict,
        __user__: Optional[dict],
        __request__: Request,
        __event_emitter__=None,
    ) -> AsyncGenerator[str, None]:

        model_id = str(
            body.get(
                "model",
                "opencode-agent",
            )
        )

        if model_id.startswith(
            "opencode."
        ):

            model_id = model_id[
                len("opencode.") :
            ]

        messages = body.get(
            "messages",
            [],
        )

        if not isinstance(
            messages,
            list,
        ):

            yield (
                "**BRIGER Error:** "
                "`messages` must be a list."
            )

            return

        if not messages:

            yield (
                "**BRIGER Error:** "
                "No messages were provided."
            )

            return

        stream = bool(
            body.get(
                "stream",
                True,
            )
        )

        if not self.valves.STREAMING_ENABLED:

            stream = False

        # ---------------------------------------------------------------------
        # Status: connecting
        # ---------------------------------------------------------------------

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

        # ---------------------------------------------------------------------
        # Health
        # ---------------------------------------------------------------------

        healthy = await self._check_health()

        if not healthy:

            if __event_emitter__:

                await __event_emitter__(
                    "status",
                    {
                        "description": (
                            "OpenCode server is unavailable."
                        ),
                        "done": True,
                    },
                )

            yield (
                "**BRIGER OpenCode is unavailable.**\n\n"
                f"Server: `{self.valves.OPENCODE_SERVER_URL}`\n\n"
                "Check the BRIGER container logs."
            )

            return

        # ---------------------------------------------------------------------
        # Build prompt
        # ---------------------------------------------------------------------

        prompt = self._build_prompt(
            messages,
            model_id,
        )

        # ---------------------------------------------------------------------
        # Status: working
        # ---------------------------------------------------------------------

        if __event_emitter__:

            await __event_emitter__(
                "status",
                {
                    "description": (
                        "OpenCode is working..."
                    ),
                    "done": False,
                },
            )

        try:

            if stream:

                async for chunk in self._request_stream(
                    prompt
                ):

                    if chunk:
                        yield chunk

            else:

                result = await self._request_once(
                    prompt
                )

                if result:
                    yield result

        except httpx.ConnectError:

            yield (
                "**BRIGER Connection Error**\n\n"
                f"Could not connect to "
                f"`{self.valves.OPENCODE_SERVER_URL}`."
            )

        except httpx.TimeoutException:

            yield (
                "**BRIGER Timeout**\n\n"
                "The OpenCode task exceeded the "
                f"{self.valves.REQUEST_TIMEOUT:.0f}-second timeout."
            )

        except httpx.HTTPStatusError as exc:

            status_code = exc.response.status_code

            try:

                detail = exc.response.json()

            except Exception:

                detail = exc.response.text

            yield (
                f"**BRIGER OpenCode HTTP {status_code}**\n\n"
                f"```text\n"
                f"{str(detail)[:4000]}\n"
                f"```"
            )

        except Exception as exc:

            yield (
                "**BRIGER Integration Error**\n\n"
                f"`{type(exc).__name__}: "
                f"{str(exc)[:2000]}`"
            )

        finally:

            if __event_emitter__:

                await __event_emitter__(
                    "status",
                    {
                        "description": (
                            "OpenCode task completed."
                        ),
                        "done": True,
                    },
                )

    # =========================================================================
    # SHUTDOWN
    # =========================================================================

    async def on_shutdown(
        self,
    ):

        if self._client is not None:

            await self._client.aclose()

            self._client = None
