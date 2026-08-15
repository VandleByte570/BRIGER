"""
title: OpenCode Tools
author: Unified AI Suite
version: 1.0.0
license: MIT
description: >
 Exposes OpenCode capabilities as Open WebUI Tools that any LLM can invoke.
 Provides file operations, shell execution, git worktree management, and
 LSP-based code understanding through the local OpenCode server.
requirements: httpx
"""

import os
import json
import httpx
from typing import Optional
from pydantic import BaseModel, Field

class Tools:
    """
    OpenCode Tools for Open WebUI.

    These tools allow any model in Open WebUI to invoke OpenCode's agentic
    coding capabilities through the local server API on port 4096.
    """

    class Valves(BaseModel):
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
        WORKSPACE_DIR: str = Field(
            default="/app/workspace",
            description="Working directory for operations"
        )
        TIMEOUT: float = Field(
            default=60.0,
            description="Request timeout in seconds"
        )

    def __init__(self):
        self.valves = self.Valves()
        self.citation = True

    def _get_client(self) -> httpx.AsyncClient:
        auth = None
        if self.valves.OPENCODE_SERVER_PASSWORD:
            auth = httpx.BasicAuth(
                self.valves.OPENCODE_SERVER_USERNAME,
                self.valves.OPENCODE_SERVER_PASSWORD
            )
        return httpx.AsyncClient(
            base_url=self.valves.OPENCODE_SERVER_URL,
            auth=auth,
            timeout=self.valves.TIMEOUT
        )

    async def opencode_shell(
        self,
        command: str = Field(
            ...,
            description="Shell command to execute safely in the workspace"
        ),
        cwd: Optional[str] = Field(
            default=None,
            description="Working directory for the command"
        ),
        __user__: dict = {}
    ) -> str:
        """
        Execute a shell command via OpenCode's sandboxed execution engine.
        Use this for: build commands, tests, git operations, package management.
        """
        work_dir = cwd or self.valves.WORKSPACE_DIR
        try:
            async with self._get_client() as client:
                resp = await client.post(
                    "/execute",
                    json={
                        "type": "shell",
                        "command": command,
                        "cwd": work_dir,
                        "user": __user__.get("name", "unknown")
                    }
                )
                resp.raise_for_status()
                data = resp.json()
                return json.dumps({
                    "stdout": data.get("stdout", ""),
                    "stderr": data.get("stderr", ""),
                    "exit_code": data.get("exit_code", 0),
                    "cwd": work_dir
                }, indent=2)
        except Exception as e:
            return f"Error executing shell command: {type(e).__name__}: {str(e)}"

    async def opencode_file_read(
        self,
        file_path: str = Field(
            ...,
            description="Absolute or relative path to the file to read"
        ),
        offset: int = Field(
            default=0,
            description="Line offset to start reading from"
        ),
        limit: int = Field(
            default=200,
            description="Maximum number of lines to read"
        ),
        __user__: dict = {}
    ) -> str:
        """
        Read a file from the workspace. Use this to inspect code, configs,
        logs, or documentation before making changes.
        """
        try:
            async with self._get_client() as client:
                resp = await client.post(
                    "/file/read",
                    json={
                        "path": file_path,
                        "offset": offset,
                        "limit": limit,
                        "workspace": self.valves.WORKSPACE_DIR
                    }
                )
                resp.raise_for_status()
                data = resp.json()
                content = data.get("content", "")
                lines = content.splitlines()
                total_lines = len(lines)
                snippet = "\n".join(lines[offset:offset + limit])
                return f"File: {file_path} (lines {offset+1}-{min(offset+limit, total_lines)} of {total_lines})\n```\n{snippet}\n```"
        except Exception as e:
            return f"Error reading file: {type(e).__name__}: {str(e)}"

    async def opencode_file_write(
        self,
        file_path: str = Field(
            ...,
            description="Path to write the file to"
        ),
        content: str = Field(
            ...,
            description="Full content to write"
        ),
        append: bool = Field(
            default=False,
            description="If true, append instead of overwrite"
        ),
        __user__: dict = {}
    ) -> str:
        """
        Write or append to a file in the workspace. Always confirm the exact
        path and content with the user before invoking this tool.
        """
        try:
            async with self._get_client() as client:
                resp = await client.post(
                    "/file/write",
                    json={
                        "path": file_path,
                        "content": content,
                        "append": append,
                        "workspace": self.valves.WORKSPACE_DIR
                    }
                )
                resp.raise_for_status()
                data = resp.json()
                return f"File written successfully: {file_path}\nBytes: {data.get('bytes_written', len(content))}"
        except Exception as e:
            return f"Error writing file: {type(e).__name__}: {str(e)}"

    async def opencode_git_status(
        self,
        cwd: Optional[str] = Field(
            default=None,
            description="Repository directory"
        ),
        __user__: dict = {}
    ) -> str:
        """
        Check git status, branch, and recent commits. Use this to understand
        the current state of the repository before making changes.
        """
        work_dir = cwd or self.valves.WORKSPACE_DIR
        try:
            async with self._get_client() as
