"""
OpenCode Headless Server
========================

FastAPI bridge between Open WebUI and the OpenCode CLI.

Endpoints:
    GET  /health
    GET  /
    POST /tui
    POST /execute
    POST /file/read
    POST /file/write
    POST /git/status
    POST /git/worktree
    POST /lsp/query

The /tui endpoint executes the installed OpenCode CLI in non-interactive
mode using:

    opencode run

The workspace is restricted to WORKSPACE_DIR.
"""

import os
import re
import json
import shutil
import subprocess
import secrets

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any, Generator

from fastapi import (
    FastAPI,
    HTTPException,
    Depends,
    status,
    Request,
)

from fastapi.responses import (
    JSONResponse,
    StreamingResponse,
)

from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from pydantic import BaseModel, Field


# =============================================================================
# Configuration
# =============================================================================

WORKSPACE_DIR = Path(
    os.getenv("WORKSPACE_DIR", "/app/workspace")
).resolve()

OPENCODE_CONFIG_DIR = Path(
    os.getenv("OPENCODE_CONFIG_DIR", "/app/.opencode")
).resolve()

OPENCODE_BINARY = os.getenv(
    "OPENCODE_BINARY",
    "opencode",
)

USERNAME = os.getenv(
    "OPENCODE_SERVER_USERNAME",
    "opencode",
)

PASSWORD = os.getenv(
    "OPENCODE_SERVER_PASSWORD",
    "",
)

LOG_LEVEL = os.getenv(
    "LOG_LEVEL",
    "info",
).lower()

OPENCODE_TIMEOUT = int(
    os.getenv(
        "OPENCODE_TIMEOUT",
        "1800",
    )
)

MAX_OUTPUT_SIZE = int(
    os.getenv(
        "MAX_OUTPUT_SIZE",
        "10_000_000",
    )
)

WORKSPACE_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


# =============================================================================
# FastAPI App
# =============================================================================

app = FastAPI(
    title="OpenCode Headless Server",
    description=(
        "Agentic coding engine API for the Unified AI Suite"
    ),
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)


# =============================================================================
# CORS
# =============================================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://localhost:7860",
        "http://127.0.0.1:7860",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# Authentication
# =============================================================================

security = HTTPBasic(
    auto_error=False
)


def verify_auth(
    credentials: Optional[HTTPBasicCredentials] = None,
):
    """
    Verify HTTP Basic authentication if a password is configured.
    """

    # Authentication disabled when password is empty.
    if not PASSWORD:
        return True

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={
                "WWW-Authenticate": "Basic"
            },
        )

    valid_username = secrets.compare_digest(
        credentials.username,
        USERNAME,
    )

    valid_password = secrets.compare_digest(
        credentials.password,
        PASSWORD,
    )

    if not (
        valid_username
        and valid_password
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={
                "WWW-Authenticate": "Basic"
            },
        )

    return True


# =============================================================================
# Pydantic Models
# =============================================================================

class TuiRequest(BaseModel):
    prompt: str = Field(
        ...,
        min_length=1,
        description="Natural language prompt to process",
    )

    stream: bool = Field(
        default=False,
        description="Enable streaming response",
    )

    workspace: str = Field(
        default=str(WORKSPACE_DIR),
        description="Working directory",
    )

    mode: str = Field(
        default="headless",
        description="Execution mode",
    )

    model: Optional[str] = Field(
        default=None,
        description="Optional OpenCode model in provider/model format",
    )

    agent: Optional[str] = Field(
        default=None,
        description="Optional OpenCode agent",
    )


class ExecuteRequest(BaseModel):
    type: str = Field(
        default="shell",
        description="Execution type",
    )

    command: str = Field(
        ...,
        min_length=1,
        description="Command to execute",
    )

    cwd: Optional[str] = Field(
        default=None,
        description="Working directory",
    )

    user: str = Field(
        default="unknown",
        description="Requesting user",
    )

    timeout: int = Field(
        default=60,
        ge=1,
        le=600,
    )


class FileReadRequest(BaseModel):
    path: str = Field(
        ...,
        description="File path to read",
    )

    offset: int = Field(
        default=0,
        ge=0,
        description="Line offset",
    )

    limit: int = Field(
        default=200,
        ge=1,
        le=10000,
        description="Max lines to read",
    )

    workspace: str = Field(
        default=str(WORKSPACE_DIR),
        description="Workspace root",
    )


class FileWriteRequest(BaseModel):
    path: str = Field(
        ...,
        description="File path to write",
    )

    content: str = Field(
        ...,
        description="Content to write",
    )

    append: bool = Field(
        default=False,
        description="Append mode",
    )

    workspace: str = Field(
        default=str(WORKSPACE_DIR),
        description="Workspace root",
    )


class GitStatusRequest(BaseModel):
    cwd: Optional[str] = Field(
        default=None,
        description="Repository directory",
    )


class GitWorktreeRequest(BaseModel):
    action: str = Field(
        ...,
        description="Action: create, list, or remove",
    )

    branch: Optional[str] = Field(
        default=None,
        description="Branch name",
    )

    path: Optional[str] = Field(
        default=None,
        description="Worktree path",
    )

    workspace: str = Field(
        default=str(WORKSPACE_DIR),
        description="Workspace root",
    )


class LspQueryRequest(BaseModel):
    file: str = Field(
        ...,
        description="File to analyze",
    )

    type: str = Field(
        default="symbols",
        description="Query type",
    )

    symbol: Optional[str] = Field(
        default=None,
        description="Symbol name",
    )

    workspace: str = Field(
        default=str(WORKSPACE_DIR),
        description="Workspace root",
    )


# =============================================================================
# Utility Functions
# =============================================================================

def utc_now() -> str:
    """
    Return an ISO-8601 UTC timestamp.
    """

    return (
        datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def get_workspace(
    workspace: Optional[str] = None,
) -> Path:
    """
    Return a validated workspace.

    The requested workspace must be WORKSPACE_DIR itself or a child
    directory of WORKSPACE_DIR.
    """

    if not workspace:
        return WORKSPACE_DIR

    requested = Path(
        workspace
    ).expanduser().resolve()

    try:
        requested.relative_to(
            WORKSPACE_DIR
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Workspace '{workspace}' is outside "
                f"the allowed workspace '{WORKSPACE_DIR}'."
            ),
        )

    requested.mkdir(
        parents=True,
        exist_ok=True,
    )

    return requested


def resolve_path(
    path: str,
    workspace: Optional[str] = None,
) -> Path:
    """
    Resolve a path while preventing directory traversal.

    Absolute paths are only allowed if they are already inside the
    configured WORKSPACE_DIR.
    """

    root = get_workspace(
        workspace
    )

    candidate = Path(
        path
    ).expanduser()

    if not candidate.is_absolute():
        candidate = root / candidate

    candidate = candidate.resolve()

    try:
        candidate.relative_to(
            root
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Path '{path}' is outside "
                f"the workspace '{root}'."
            ),
        )

    return candidate


# =============================================================================
# Command Safety
# =============================================================================

BLOCKED_COMMANDS = [
    r"rm\s+-rf\s+/",
    r"rm\s+-rf\s+/\s*",
    r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",
    r"\bmkfs(\.|[\s])",
    r"\bdd\s+if=.*of=/dev/",
    r">\s*/dev/",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bpoweroff\b",
    r"curl\s+.*\|\s*sh",
    r"curl\s+.*\|\s*bash",
    r"wget\s+.*\|\s*sh",
    r"wget\s+.*\|\s*bash",
]

BLOCKED_COMMAND_REGEX = [
    re.compile(
        pattern,
        re.IGNORECASE,
    )
    for pattern in BLOCKED_COMMANDS
]


def is_command_safe(
    command: str,
) -> tuple[bool, str]:
    """
    Basic command safety filter.

    This is not a substitute for container isolation.
    """

    for pattern in BLOCKED_COMMAND_REGEX:
        if pattern.search(command):
            return (
                False,
                "Command blocked by safety policy.",
            )

    return True, ""


def run_shell(
    command: str,
    cwd: Optional[str] = None,
    timeout: int = 60,
) -> Dict[str, Any]:
    """
    Run a shell command inside the allowed workspace.
    """

    safe, reason = is_command_safe(
        command
    )

    if not safe:
        return {
            "stdout": "",
            "stderr": reason,
            "exit_code": 1,
            "blocked": True,
        }

    work_dir = get_workspace(
        cwd
    )

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
            executable="/bin/bash",
            env=os.environ.copy(),
        )

        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
            "cwd": str(work_dir),
            "blocked": False,
        }

    except subprocess.TimeoutExpired:
        return {
            "stdout": "",
            "stderr": (
                f"Command timed out after "
                f"{timeout}s"
            ),
            "exit_code": 124,
            "cwd": str(work_dir),
            "timeout": True,
        }

    except Exception as exc:
        return {
            "stdout": "",
            "stderr": str(exc),
            "exit_code": 1,
            "cwd": str(work_dir),
        }


# =============================================================================
# OpenCode Skills
# =============================================================================

def load_skills() -> tuple[str, int]:
    """
    Load both legacy *.md skills and modern:
        skills/<name>/SKILL.md
    """

    skills_dir = (
        OPENCODE_CONFIG_DIR
        / "skills"
    )

    if not skills_dir.exists():
        return "", 0

    skill_files: List[Path] = []

    # Legacy format:
    # .opencode/skills/name.md
    skill_files.extend(
        sorted(
            skills_dir.glob("*.md")
        )
    )

    # Modern OpenCode format:
    # .opencode/skills/name/SKILL.md
    skill_files.extend(
        sorted(
            skills_dir.glob(
                "*/SKILL.md"
            )
        )
    )

    sections: List[str] = []

    for skill_file in skill_files:
        try:
            content = skill_file.read_text(
                encoding="utf-8",
                errors="replace",
            )

            sections.append(
                f"\n\n--- "
                f"{skill_file.relative_to(skills_dir)} "
                f"---\n"
            )

            sections.append(
                content
            )

        except OSError:
            continue

    return (
        "".join(sections),
        len(skill_files),
    )


# =============================================================================
# OpenCode Prompt
# =============================================================================

def build_full_prompt(
    req: TuiRequest,
) -> str:
    """
    Build the prompt sent to OpenCode.
    """

    workspace = get_workspace(
        req.workspace
    )

    skills_context, _ = load_skills()

    prompt = f"""
You are the OpenCode Agent running inside the BRIGER
headless coding environment.

WORKSPACE:
{workspace}

You MUST operate only inside this workspace.

## Capabilities

You can:
- inspect the repository
- read files
- modify files
- execute project commands
- run tests
- inspect git status
- use available OpenCode skills

## Safety

- Do not expose secrets or credentials.
- Do not modify files outside the workspace.
- Do not perform destructive system-level operations.
- Before irreversible/destructive operations, explain what you intend to do.
- Prefer minimal, targeted changes.
- Run appropriate tests after making changes.
- Inspect existing code before modifying it.

## GodMode

If GodMode skills are available, follow the applicable
workflow defined by those skills.

{skills_context}

## User Request

{req.prompt}

## Final Response

After completing the task:
1. Summarize what you changed.
2. List the files changed.
3. Report tests/checks that were run.
4. Report any remaining problems.
"""

    return prompt.strip()


# =============================================================================
# OpenCode Command
# =============================================================================

def build_opencode_command(
    req: TuiRequest,
) -> List[str]:
    """
    Build the current OpenCode CLI command.

    Current OpenCode supports:
        opencode run
        --dir
        --format
        --model
        --agent
    """

    workspace = get_workspace(
        req.workspace
    )

    prompt = build_full_prompt(
        req
    )

    command = [
        OPENCODE_BINARY,
        "run",
        "--dir",
        str(workspace),
        "--format",
        "json",
    ]

    if req.model:
        command.extend(
            [
                "--model",
                req.model,
            ]
        )

    if req.agent:
        command.extend(
            [
                "--agent",
                req.agent,
            ]
        )

    command.append(
        prompt
    )

    return command


# =============================================================================
# OpenCode JSON Parsing
# =============================================================================

def extract_content(
    event: Any,
) -> str:
    """
    Extract useful text from an OpenCode JSON event.

    OpenCode's JSON format is event based, so this deliberately
    handles several possible event shapes.
    """

    if isinstance(event, str):
        return event

    if not isinstance(event, dict):
        return ""

    # Direct content fields.
    for key in (
        "content",
        "text",
        "message",
    ):
        value = event.get(key)

        if isinstance(value, str):
            return value

    # Nested part.
    part = event.get(
        "part"
    )

    if isinstance(part, dict):
        for key in (
            "text",
            "content",
        ):
            value = part.get(key)

            if isinstance(value, str):
                return value

    # Nested message.
    message = event.get(
        "message"
    )

    if isinstance(message, dict):
        for key in (
            "text",
            "content",
        ):
            value = message.get(key)

            if isinstance(value, str):
                return value

    return ""


def parse_opencode_output(
    stdout: str,
) -> str:
    """
    Convert OpenCode JSONL output into readable assistant text.
    """

    output: List[str] = []

    for line in stdout.splitlines():
        line = line.strip()

        if not line:
            continue

        try:
            event = json.loads(
                line
            )

            content = extract_content(
                event
            )

            if content:
                output.append(
                    content
                )

        except json.JSONDecodeError:
            # Fallback for non-JSON output.
            output.append(
                line
            )

    return "\n".join(
        output
    ).strip()


# =============================================================================
# OpenCode Non-Streaming Execution
# =============================================================================

def run_opencode(
    req: TuiRequest,
) -> Dict[str, Any]:
    """
    Execute OpenCode in non-interactive mode.
    """

    workspace = get_workspace(
        req.workspace
    )

    command = build_opencode_command(
        req
    )

    try:
        result = subprocess.run(
            command,
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=OPENCODE_TIMEOUT,
            env=os.environ.copy(),
        )

        stdout = (
            result.stdout
            or ""
        )

        stderr = (
            result.stderr
            or ""
        )

        if len(stdout) > MAX_OUTPUT_SIZE:
            stdout = stdout[
                -MAX_OUTPUT_SIZE:
            ]

        if len(stderr) > MAX_OUTPUT_SIZE:
            stderr = stderr[
                -MAX_OUTPUT_SIZE:
            ]

        content = parse_opencode_output(
            stdout
        )

        if not content:
            content = stdout.strip()

        if not content and stderr:
            content = (
                "OpenCode error:\n\n"
                + stderr
            )

        return {
            "content": content,
            "response": content,
            "mode": req.mode,
            "workspace": str(workspace),
            "success": (
                result.returncode == 0
            ),
            "exit_code": result.returncode,
            "stderr": stderr,
            "timestamp": utc_now(),
        }

    except FileNotFoundError:
        return {
            "content": (
                f"OpenCode executable "
                f"'{OPENCODE_BINARY}' was not found "
                f"in PATH."
            ),
            "response": (
                f"OpenCode executable "
                f"'{OPENCODE_BINARY}' was not found "
                f"in PATH."
            ),
            "mode": req.mode,
            "workspace": str(workspace),
            "success": False,
            "exit_code": 127,
            "timestamp": utc_now(),
        }

    except subprocess.TimeoutExpired:
        return {
            "content": (
                "OpenCode execution timed out "
                f"after {OPENCODE_TIMEOUT} seconds."
            ),
            "response": (
                "OpenCode execution timed out "
                f"after {OPENCODE_TIMEOUT} seconds."
            ),
            "mode": req.mode,
            "workspace": str(workspace),
            "success": False,
            "exit_code": 124,
            "timeout": True,
            "timestamp": utc_now(),
        }

    except Exception as exc:
        return {
            "content": (
                f"OpenCode execution error: "
                f"{type(exc).__name__}: {exc}"
            ),
            "response": (
                f"OpenCode execution error: "
                f"{type(exc).__name__}: {exc}"
            ),
            "mode": req.mode,
            "workspace": str(workspace),
            "success": False,
            "exit_code": 1,
            "timestamp": utc_now(),
        }


# =============================================================================
# OpenCode Streaming
# =============================================================================

def stream_opencode(
    req: TuiRequest,
) -> Generator[str, None, None]:
    """
    Stream OpenCode JSON events to Open WebUI as SSE.

    The Open WebUI pipe expects:
        data: {"content": "..."}
    """

    workspace = get_workspace(
        req.workspace
    )

    command = build_opencode_command(
        req
    )

    process = None

    try:
        process = subprocess.Popen(
            command,
            cwd=str(workspace),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
            env=os.environ.copy(),
        )

        if process.stdout is None:
            yield (
                "event: error\n"
                'data: {"content":"Unable to read OpenCode output."}\n\n'
            )
            return

        for line in process.stdout:

            line = line.strip()

            if not line:
                continue

            content = ""

            try:
                event = json.loads(
                    line
                )

                content = extract_content(
                    event
                )

            except json.JSONDecodeError:
                content = line

            if content:
                payload = json.dumps(
                    {
                        "content": content
                    },
                    ensure_ascii=False,
                )

                yield (
                    f"data: {payload}\n\n"
                )

        return_code = process.wait(
            timeout=OPENCODE_TIMEOUT
        )

        done_payload = json.dumps(
            {
                "content": "",
                "exit_code": return_code,
                "success": (
                    return_code == 0
                ),
            }
        )

        yield (
            f"event: done\n"
            f"data: {done_payload}\n\n"
        )

    except FileNotFoundError:

        payload = json.dumps(
            {
                "content": (
                    f"OpenCode executable "
                    f"'{OPENCODE_BINARY}' was not found."
                )
            }
        )

        yield (
            f"event: error\n"
            f"data: {payload}\n\n"
        )

    except subprocess.TimeoutExpired:

        if process is not None:
            try:
                process.kill()
            except Exception:
                pass

        payload = json.dumps(
            {
                "content": (
                    "OpenCode execution timed out."
                )
            }
        )

        yield (
            f"event: error\n"
            f"data: {payload}\n\n"
        )

    except Exception as exc:

        if process is not None:
            try:
                process.kill()
            except Exception:
                pass

        payload = json.dumps(
            {
                "content": (
                    f"OpenCode error: "
                    f"{type(exc).__name__}: {exc}"
                )
            }
        )

        yield (
            f"event: error\n"
            f"data: {payload}\n\n"
        )


# =============================================================================
# Health
# =============================================================================

@app.get("/health")
async def health_check():
    """
    Health check endpoint.
    """

    opencode_path = shutil.which(
        OPENCODE_BINARY
    )

    return {
        "status": "healthy",
        "service": "opencode-server",
        "version": "2.0.0",
        "timestamp": utc_now(),
        "workspace": str(WORKSPACE_DIR),
        "opencode": {
            "binary": OPENCODE_BINARY,
            "available": (
                opencode_path is not None
            ),
            "path": opencode_path,
        },
    }


# =============================================================================
# Root
# =============================================================================

@app.get("/")
async def root():
    """
    Root endpoint.
    """

    return {
        "message": "OpenCode Headless Server",
        "version": "2.0.0",
        "docs": "/docs",
        "health": "/health",
    }


# =============================================================================
# TUI / OpenCode Agent
# =============================================================================

@app.post("/tui")
async def tui_process(
    req: TuiRequest,
    credentials: Optional[
        HTTPBasicCredentials
    ] = Depends(security),
):
    """
    Process a natural-language prompt through OpenCode.
    """

    verify_auth(
        credentials
    )

    if not req.prompt.strip():
        raise HTTPException(
            status_code=400,
            detail="Prompt cannot be empty.",
        )

    # Validate workspace before starting OpenCode.
    get_workspace(
        req.workspace
    )

    if req.stream:
        return StreamingResponse(
            stream_opencode(req),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    result = run_opencode(
        req
    )

    # Preserve useful response for Open WebUI.
    return JSONResponse(
        status_code=200,
        content=result,
    )


# =============================================================================
# Execute Shell Command
# =============================================================================

@app.post("/execute")
async def execute_command(
    req: ExecuteRequest,
    credentials: Optional[
        HTTPBasicCredentials
    ] = Depends(security),
):
    """
    Execute a shell command inside WORKSPACE_DIR.
    """

    verify_auth(
        credentials
    )

    if req.type != "shell":
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported execution "
                f"type: {req.type}"
            ),
        )

    result = run_shell(
        req.command,
        cwd=req.cwd,
        timeout=req.timeout,
    )

    return result


# =============================================================================
# File Read
# =============================================================================

@app.post("/file/read")
async def file_read(
    req: FileReadRequest,
    credentials: Optional[
        HTTPBasicCredentials
    ] = Depends(security),
):
    """
    Read a file from the workspace.
    """

    verify_auth(
        credentials
    )

    target = resolve_path(
        req.path,
        req.workspace,
    )

    if not target.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                f"File not found: "
                f"{req.path}"
            ),
        )

    if not target.is_file():
        raise HTTPException(
            status_code=400,
            detail=(
                f"Path is not a file: "
                f"{req.path}"
            ),
        )

    try:
        content = target.read_text(
            encoding="utf-8",
            errors="replace",
        )

        lines = content.splitlines()

        snippet = "\n".join(
            lines[
                req.offset:
                req.offset + req.limit
            ]
        )

        return {
            "path": str(target),
            "content": content,
            "snippet": snippet,
            "offset": req.offset,
            "limit": req.limit,
            "total_lines": len(lines),
            "size_bytes": target.stat().st_size,
        }

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Error reading file: "
                f"{exc}"
            ),
        )


# =============================================================================
# File Write
# =============================================================================

@app.post("/file/write")
async def file_write(
    req: FileWriteRequest,
    credentials: Optional[
        HTTPBasicCredentials
    ] = Depends(security),
):
    """
    Write or append to a workspace file.
    """

    verify_auth(
        credentials
    )

    target = resolve_path(
        req.path,
        req.workspace,
    )

    try:
        target.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        mode = (
            "a"
            if req.append
            else "w"
        )

        with target.open(
            mode,
            encoding="utf-8",
        ) as file:
            file.write(
                req.content
            )

        return {
            "path": str(target),
            "bytes_written": len(
                req.content.encode(
                    "utf-8"
                )
            ),
            "append": req.append,
            "success": True,
        }

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Error writing file: "
                f"{exc}"
            ),
        )


# =============================================================================
# Git Status
# =============================================================================

@app.post("/git/status")
async def git_status(
    req: GitStatusRequest,
    credentials: Optional[
        HTTPBasicCredentials
    ] = Depends(security),
):
    """
    Get Git status for a repository.
    """

    verify_auth(
        credentials
    )

    cwd = get_workspace(
        req.cwd
    )

    result = run_shell(
        "git status --short --branch",
        cwd=str(cwd),
    )

    log_result = run_shell(
        "git log --oneline -5",
        cwd=str(cwd),
    )

    branch_result = run_shell(
        "git branch --show-current",
        cwd=str(cwd),
    )

    return {
        "cwd": str(cwd),
        "branch": (
            branch_result["stdout"]
            .strip()
        ),
        "status": result["stdout"],
        "recent_commits": (
            log_result["stdout"]
            .strip()
            .splitlines()
            if log_result["stdout"]
            else []
        ),
        "is_git_repo": (
            result["exit_code"] == 0
        ),
    }


# =============================================================================
# Git Worktree
# =============================================================================

def validate_git_name(
    value: str,
    field: str,
) -> str:
    """
    Validate branch/worktree input before passing it to Git.

    We also use subprocess argument arrays below, so shell injection
    is avoided.
    """

    if not value:
        raise HTTPException(
            status_code=400,
            detail=f"{field} is required.",
        )

    if (
        len(value) > 255
        or "\x00" in value
        or "\n" in value
        or "\r" in value
    ):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {field}.",
        )

    return value


def run_git(
    args: List[str],
    cwd: Path,
) -> Dict[str, Any]:
    """
    Execute Git without shell=True.

    This prevents branch/path input from becoming shell syntax.
    """

    try:
        result = subprocess.run(
            [
                "git",
                *args,
            ],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=120,
            env=os.environ.copy(),
        )

        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
            "cwd": str(cwd),
        }

    except Exception as exc:
        return {
            "stdout": "",
            "stderr": str(exc),
            "exit_code": 1,
            "cwd": str(cwd),
        }


@app.post("/git/worktree")
async def git_worktree(
    req: GitWorktreeRequest,
    credentials: Optional[
        HTTPBasicCredentials
    ] = Depends(security),
):
    """
    Manage Git worktrees safely.
    """

    verify_auth(
        credentials
    )

    workspace = get_workspace(
        req.workspace
    )

    worktree_base = (
        workspace
        / ".worktrees"
    )

    worktree_base.mkdir(
        parents=True,
        exist_ok=True,
    )

    if req.action == "list":

        result = run_git(
            [
                "worktree",
                "list",
                "--porcelain",
            ],
            workspace,
        )

        return {
            "action": "list",
            "worktrees": result["stdout"],
            "exit_code": result["exit_code"],
        }

    if req.action == "create":

        if not req.branch:
            raise HTTPException(
                status_code=400,
                detail=(
                    "branch is required "
                    "for create"
                ),
            )

        if not req.path:
            raise HTTPException(
                status_code=400,
                detail=(
                    "path is required "
                    "for create"
                ),
            )

        branch = validate_git_name(
            req.branch,
            "branch",
        )

        # Worktree MUST stay inside:
        # /app/workspace/.worktrees
        target_path = resolve_path(
            req.path,
            str(worktree_base),
        )

        if target_path == worktree_base:
            raise HTTPException(
                status_code=400,
                detail="Invalid worktree path.",
            )

        if target_path.exists():
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Worktree already exists: "
                    f"{target_path}"
                ),
            )

        target_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        result = run_git(
            [
                "worktree",
                "add",
                str(target_path),
                "-b",
                branch,
            ],
            workspace,
        )

        return {
            "action": "create",
            "branch": branch,
            "path": str(target_path),
            "result": result,
        }

    if req.action == "remove":

        if not req.path:
            raise HTTPException(
                status_code=400,
                detail=(
                    "path is required "
                    "for remove"
                ),
            )

        target_path = resolve_path(
            req.path,
            str(worktree_base),
        )

        if target_path == worktree_base:
            raise HTTPException(
                status_code=400,
                detail="Invalid worktree path.",
            )

        result = run_git(
            [
                "worktree",
                "remove",
                str(target_path),
                "--force",
            ],
            workspace,
        )

        return {
            "action": "remove",
            "path": str(target_path),
            "result": result,
        }

    raise HTTPException(
        status_code=400,
        detail=(
            f"Unknown action: "
            f"{req.action}"
        ),
    )


# =============================================================================
# LSP / Symbol Query
# =============================================================================

@app.post("/lsp/query")
async def lsp_query(
    req: LspQueryRequest,
    credentials: Optional[
        HTTPBasicCredentials
    ] = Depends(security),
):
    """
    Lightweight source-code symbol extraction.

    This remains a fallback/stub rather than a full LSP implementation.
    """

    verify_auth(
        credentials
    )

    target = resolve_path(
        req.file,
        req.workspace,
    )

    if not target.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                f"File not found: "
                f"{req.file}"
            ),
        )

    if not target.is_file():
        raise HTTPException(
            status_code=400,
            detail=(
                f"Path is not a file: "
                f"{req.file}"
            ),
        )

    try:
        content = target.read_text(
            encoding="utf-8",
            errors="replace",
        )

        lines = content.splitlines()

        symbols: List[
            Dict[str, Any]
        ] = []

        # Python
        if target.suffix.lower() in (
            ".py",
            ".pyw",
        ):

            pattern = re.compile(
                r"^\s*"
                r"(class|def|async\s+def)"
                r"\s+([A-Za-z_]\w*)"
            )

            for line_number, line in enumerate(
                lines,
                1,
            ):

                match = pattern.match(
                    line
                )

                if match:
                    symbols.append(
                        {
                            "line": line_number,
                            "name": match.group(2),
                            "text": line.strip(),
                            "type": "python",
                        }
                    )

        # JavaScript / TypeScript
        elif target.suffix.lower() in (
            ".js",
            ".ts",
            ".jsx",
            ".tsx",
        ):

            patterns = [
                re.compile(
                    r"^\s*"
                    r"(?:export\s+)?"
                    r"(class|function)"
                    r"\s+([A-Za-z_$][\w$]*)"
                ),
                re.compile(
                    r"^\s*"
                    r"(?:export\s+)?"
                    r"(const|let|var)"
                    r"\s+([A-Za-z_$][\w$]*)"
                ),
            ]

            for line_number, line in enumerate(
                lines,
                1,
            ):

                for pattern in patterns:

                    match = pattern.match(
                        line
                    )

                    if match:
                        symbols.append(
                            {
                                "line": line_number,
                                "name": match.group(2),
                                "text": line.strip(),
                                "type": "javascript",
                            }
                        )
                        break

        if req.symbol:
            symbols = [
                symbol
                for symbol in symbols
                if symbol["name"]
                == req.symbol
            ]

        return {
            "file": str(target),
            "query_type": req.type,
            "symbol": req.symbol,
            "total_lines": len(lines),
            "symbols_found": len(symbols),
            "symbols": symbols[:100],
            "note": (
                "Full LSP integration requires "
                "language-specific LSP servers."
            ),
        }

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"LSP query error: "
                f"{exc}"
            ),
        )


# =============================================================================
# Global Exception Handler
# =============================================================================

@app.exception_handler(Exception)
async def unhandled_exception(
    request: Request,
    exc: Exception,
):
    """
    Return a consistent JSON error.
    """

    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "detail": str(exc),
            "path": request.url.path,
            "timestamp": utc_now(),
        },
    )


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":

    import uvicorn

    port = int(
        os.getenv(
            "OPENCODE_SERVER_PORT",
            "4096",
        )
    )

    host = os.getenv(
        "OPENCODE_SERVER_HOSTNAME",
        "0.0.0.0",
    )

    uvicorn.run(
        app,
        host=host,
        port=port,
    )
