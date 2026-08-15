"""
BRIGER OpenCode Headless Server
================================

FastAPI wrapper around the OpenCode CLI.

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

The /tui endpoint executes OpenCode in non-interactive mode using:

    opencode run ...

The workspace is restricted to WORKSPACE_DIR for file and command operations.
"""

from __future__ import annotations

import os
import re
import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
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

OPENCODE_BINARY = os.getenv("OPENCODE_BINARY", "opencode")

OPENCODE_SERVER_HOSTNAME = os.getenv(
    "OPENCODE_SERVER_HOSTNAME",
    "0.0.0.0",
)

OPENCODE_SERVER_PORT = int(
    os.getenv("OPENCODE_SERVER_PORT", "4096")
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
    os.getenv("OPENCODE_TIMEOUT", "1800")
)

MAX_OUTPUT_SIZE = int(
    os.getenv("MAX_OUTPUT_SIZE", "10_000_000")
)


WORKSPACE_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


# =============================================================================
# FastAPI
# =============================================================================

app = FastAPI(
    title="BRIGER OpenCode Headless Server",
    description="HTTP interface for the OpenCode coding agent.",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)


# =============================================================================
# CORS
# =============================================================================

allowed_origins = [
    "http://localhost:8080",
    "http://127.0.0.1:8080",
    "http://localhost:7860",
    "http://127.0.0.1:7860",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

extra_origins = os.getenv("CORS_ORIGINS", "").strip()

if extra_origins:
    allowed_origins.extend(
        origin.strip()
        for origin in extra_origins.split(",")
        if origin.strip()
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# Authentication
# =============================================================================

security = HTTPBasic(auto_error=False)


def verify_auth(
    credentials: Optional[HTTPBasicCredentials] = None,
) -> bool:
    """
    Verify HTTP Basic authentication.

    Authentication is disabled when OPENCODE_SERVER_PASSWORD is empty.
    """

    if not PASSWORD:
        return True

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={
                "WWW-Authenticate": "Basic",
            },
        )

    valid_user = secrets.compare_digest(
        credentials.username,
        USERNAME,
    )

    valid_password = secrets.compare_digest(
        credentials.password,
        PASSWORD,
    )

    if not (valid_user and valid_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={
                "WWW-Authenticate": "Basic",
            },
        )

    return True


# =============================================================================
# Models
# =============================================================================

class TuiRequest(BaseModel):
    prompt: str = Field(
        ...,
        min_length=1,
        description="Natural language prompt",
    )

    stream: bool = Field(
        default=False,
        description="Stream OpenCode output",
    )

    workspace: str = Field(
        default=str(WORKSPACE_DIR),
        description="Workspace directory",
    )

    mode: str = Field(
        default="headless",
        description="Execution mode",
    )

    agent: Optional[str] = Field(
        default=None,
        description="Optional OpenCode agent",
    )

    model: Optional[str] = Field(
        default=None,
        description="Optional OpenCode model",
    )


class ExecuteRequest(BaseModel):
    type: str = Field(
        default="shell",
        description="Execution type",
    )

    command: str = Field(
        ...,
        min_length=1,
        description="Shell command",
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
    path: str

    offset: int = Field(
        default=0,
        ge=0,
    )

    limit: int = Field(
        default=200,
        ge=1,
        le=10_000,
    )

    workspace: str = Field(
        default=str(WORKSPACE_DIR),
    )


class FileWriteRequest(BaseModel):
    path: str

    content: str

    append: bool = False

    workspace: str = Field(
        default=str(WORKSPACE_DIR),
    )


class GitStatusRequest(BaseModel):
    cwd: Optional[str] = None


class GitWorktreeRequest(BaseModel):
    action: str

    branch: Optional[str] = None

    path: Optional[str] = None

    workspace: str = Field(
        default=str(WORKSPACE_DIR),
    )


class LspQueryRequest(BaseModel):
    file: str

    type: str = Field(
        default="symbols",
    )

    symbol: Optional[str] = None

    workspace: str = Field(
        default=str(WORKSPACE_DIR),
    )


# =============================================================================
# Utilities
# =============================================================================

def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp."""

    return (
        datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def resolve_workspace(workspace: Optional[str]) -> Path:
    """
    Resolve and validate a workspace.

    The requested workspace must be WORKSPACE_DIR itself or a child
    directory of WORKSPACE_DIR.
    """

    if not workspace:
        return WORKSPACE_DIR

    requested = Path(workspace).expanduser().resolve()

    try:
        requested.relative_to(WORKSPACE_DIR)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Workspace '{workspace}' is outside "
                f"the allowed workspace '{WORKSPACE_DIR}'"
            ),
        )

    requested.mkdir(
        parents=True,
        exist_ok=True,
    )

    return requested


def resolve_path(
    path: str,
    workspace: str | Path = WORKSPACE_DIR,
) -> Path:
    """
    Resolve a path while preventing directory traversal.
    """

    workspace_path = resolve_workspace(str(workspace))

    candidate = Path(path).expanduser()

    if not candidate.is_absolute():
        candidate = workspace_path / candidate

    candidate = candidate.resolve()

    try:
        candidate.relative_to(workspace_path)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Path '{path}' is outside "
                f"the workspace '{workspace_path}'"
            ),
        )

    return candidate


# =============================================================================
# Command Safety
# =============================================================================

BLOCKED_COMMAND_PATTERNS = [
    r"\brm\s+-rf\s+/\s*$",
    r"\brm\s+-rf\s+/\s+",
    r"\bmkfs(\.|[\s])",
    r"\bdd\s+if=.*\bof=/dev/",
    r">\s*/dev/",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bpoweroff\b",
    r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\};:",
    r"\bcurl\b.*\|\s*(sh|bash)\b",
    r"\bwget\b.*\|\s*(sh|bash)\b",
]

BLOCKED_COMMAND_REGEX = [
    re.compile(
        pattern,
        re.IGNORECASE,
    )
    for pattern in BLOCKED_COMMAND_PATTERNS
]


def is_command_safe(
    command: str,
) -> tuple[bool, str]:
    """
    Apply basic safety checks to shell commands.

    This is NOT a container sandbox. The application should still run
    inside an appropriately isolated container.
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
    Execute a shell command inside the allowed workspace.
    """

    safe, reason = is_command_safe(command)

    if not safe:
        return {
            "stdout": "",
            "stderr": reason,
            "exit_code": 1,
            "blocked": True,
        }

    work_dir = resolve_workspace(cwd)

    timeout = max(
        1,
        min(timeout, 600),
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

    except subprocess.TimeoutExpired as exc:
        stdout = (
            exc.stdout.decode(errors="replace")
            if isinstance(exc.stdout, bytes)
            else (exc.stdout or "")
        )

        stderr = (
            exc.stderr.decode(errors="replace")
            if isinstance(exc.stderr, bytes)
            else (exc.stderr or "")
        )

        return {
            "stdout": stdout,
            "stderr": (
                stderr
                + f"\nCommand timed out after {timeout} seconds."
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
# OpenCode Integration
# =============================================================================

def load_skills() -> str:
    """
    Load markdown skills from .opencode/skills.

    Supports both:
        .opencode/skills/*.md

    and the newer OpenCode style:
        .opencode/skills/<name>/SKILL.md
    """

    if not OPENCODE_CONFIG_DIR.exists():
        return ""

    skills_dir = OPENCODE_CONFIG_DIR / "skills"

    if not skills_dir.exists():
        return ""

    files: List[Path] = []

    files.extend(
        sorted(skills_dir.glob("*.md"))
    )

    files.extend(
        sorted(skills_dir.glob("*/SKILL.md"))
    )

    sections: List[str] = []

    for skill_file in files:
        try:
            content = skill_file.read_text(
                encoding="utf-8",
                errors="replace",
            )

            sections.append(
                f"\n\n--- SKILL: {skill_file.name} ---\n"
                f"{content}"
            )

        except OSError:
            continue

    return "".join(sections)


def build_opencode_prompt(
    request: TuiRequest,
) -> str:
    """
    Build the final prompt sent to OpenCode.
    """

    workspace = resolve_workspace(
        request.workspace
    )

    skills = load_skills()

    prompt_parts = []

    if skills:
        prompt_parts.append(
            "You are operating inside the BRIGER coding environment."
        )

        prompt_parts.append(
            "The following project skills are available:"
        )

        prompt_parts.append(skills)

    prompt_parts.append(
        "\n## User Request\n"
        + request.prompt
    )

    prompt_parts.append(
        "\n## Workspace\n"
        + str(workspace)
    )

    prompt_parts.append(
        """
## Execution Rules

1. Work only inside the provided workspace.
2. Inspect the existing project before making changes.
3. Prefer minimal, targeted changes.
4. Run relevant tests or validation when possible.
5. Do not expose secrets or credentials.
6. Do not perform destructive system-level operations.
7. Explain important changes and test results in the final response.
"""
    )

    return "\n".join(prompt_parts)


def build_opencode_command(
    request: TuiRequest,
) -> List[str]:
    """
    Build an OpenCode CLI command.

    OpenCode supports:
        opencode run [message...]

    with --dir, --agent, --model and --format.
    """

    workspace = resolve_workspace(
        request.workspace
    )

    prompt = build_opencode_prompt(request)

    command = [
        OPENCODE_BINARY,
        "run",
        "--dir",
        str(workspace),
        "--format",
        "default",
    ]

    if request.agent:
        command.extend(
            [
                "--agent",
                request.agent,
            ]
        )

    if request.model:
        command.extend(
            [
                "--model",
                request.model,
            ]
        )

    command.append(prompt)

    return command


def run_opencode(
    request: TuiRequest,
) -> Dict[str, Any]:
    """
    Execute OpenCode in non-interactive mode.
    """

    workspace = resolve_workspace(
        request.workspace
    )

    command = build_opencode_command(
        request
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

        stdout = result.stdout or ""
        stderr = result.stderr or ""

        if len(stdout) > MAX_OUTPUT_SIZE:
            stdout = stdout[-MAX_OUTPUT_SIZE:]

        if len(stderr) > MAX_OUTPUT_SIZE:
            stderr = stderr[-MAX_OUTPUT_SIZE:]

        return {
            "success": result.returncode == 0,
            "content": stdout,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": result.returncode,
            "workspace": str(workspace),
            "timestamp": utc_now(),
        }

    except FileNotFoundError:
        return {
            "success": False,
            "content": "",
            "stdout": "",
            "stderr": (
                f"OpenCode executable '{OPENCODE_BINARY}' "
                "was not found in PATH."
            ),
            "exit_code": 127,
            "workspace": str(workspace),
            "timestamp": utc_now(),
        }

    except subprocess.TimeoutExpired as exc:
        stdout = (
            exc.stdout.decode(errors="replace")
            if isinstance(exc.stdout, bytes)
            else (exc.stdout or "")
        )

        stderr = (
            exc.stderr.decode(errors="replace")
            if isinstance(exc.stderr, bytes)
            else (exc.stderr or "")
        )

        return {
            "success": False,
            "content": stdout,
            "stdout": stdout,
            "stderr": (
                stderr
                + f"\nOpenCode timed out after "
                f"{OPENCODE_TIMEOUT} seconds."
            ),
            "exit_code": 124,
            "workspace": str(workspace),
            "timeout": True,
            "timestamp": utc_now(),
        }

    except Exception as exc:
        return {
            "success": False,
            "content": "",
            "stdout": "",
            "stderr": str(exc),
            "exit_code": 1,
            "workspace": str(workspace),
            "timestamp": utc_now(),
        }


def stream_opencode(
    request: TuiRequest,
):
    """
    Stream OpenCode stdout/stderr as Server-Sent Events.

    This gives the client incremental output instead of waiting
    for the entire command to finish.
    """

    workspace = resolve_workspace(
        request.workspace
    )

    command = build_opencode_command(
        request
    )

    process: Optional[subprocess.Popen] = None

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

        assert process.stdout is not None

        for line in process.stdout:
            data = line.rstrip("\n")

            yield (
                "data: "
                + data.replace("\r", "")
                + "\n\n"
            )

        return_code = process.wait(
            timeout=OPENCODE_TIMEOUT
        )

        yield (
            "event: done\n"
            f"data: {{\"exit_code\": {return_code}}}\n\n"
        )

    except FileNotFoundError:
        yield (
            "event: error\n"
            "data: OpenCode executable was not found.\n\n"
        )

    except subprocess.TimeoutExpired:
        if process is not None:
            process.kill()

        yield (
            "event: error\n"
            "data: OpenCode execution timed out.\n\n"
        )

    except Exception as exc:
        if process is not None:
            try:
                process.kill()
            except Exception:
                pass

        yield (
            "event: error\n"
            f"data: {str(exc)}\n\n"
        )


# =============================================================================
# Health
# =============================================================================

@app.get("/health")
async def health_check():
    """
    Health check.
    """

    opencode_available = shutil.which(
        OPENCODE_BINARY
    ) is not None

    return {
        "status": "healthy",
        "service": "briger-opencode-server",
        "version": "2.0.0",
        "timestamp": utc_now(),
        "workspace": str(WORKSPACE_DIR),
        "opencode_binary": OPENCODE_BINARY,
        "opencode_available": opencode_available,
    }


@app.get("/")
async def root():
    return {
        "message": "BRIGER OpenCode Headless Server",
        "version": "2.0.0",
        "docs": "/docs",
        "health": "/health",
    }


# =============================================================================
# TUI / OpenCode
# =============================================================================

@app.post("/tui")
async def tui_process(
    req: TuiRequest,
    credentials: Optional[HTTPBasicCredentials] = Depends(
        security
    ),
):
    """
    Execute a natural-language request using OpenCode.
    """

    verify_auth(credentials)

    if not req.prompt.strip():
        raise HTTPException(
            status_code=400,
            detail="Prompt cannot be empty.",
        )

    resolve_workspace(
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

    result = run_opencode(req)

    return JSONResponse(
        status_code=200 if result["success"] else 500,
        content={
            **result,
            "mode": req.mode,
        },
    )


# =============================================================================
# Shell Execution
# =============================================================================

@app.post("/execute")
async def execute_command(
    req: ExecuteRequest,
    credentials: Optional[HTTPBasicCredentials] = Depends(
        security
    ),
):
    """
    Execute a shell command inside the workspace.
    """

    verify_auth(credentials)

    if req.type != "shell":
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported execution type: {req.type}"
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
    credentials: Optional[HTTPBasicCredentials] = Depends(
        security
    ),
):
    """
    Read a file from the workspace.
    """

    verify_auth(credentials)

    target = resolve_path(
        req.path,
        req.workspace,
    )

    if not target.exists():
        raise HTTPException(
            status_code=404,
            detail=f"File not found: {req.path}",
        )

    if not target.is_file():
        raise HTTPException(
            status_code=400,
            detail=f"Path is not a file: {req.path}",
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

    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Error reading file: {exc}",
        )


# =============================================================================
# File Write
# =============================================================================

@app.post("/file/write")
async def file_write(
    req: FileWriteRequest,
    credentials: Optional[HTTPBasicCredentials] = Depends(
        security
    ),
):
    """
    Write or append to a workspace file.
    """

    verify_auth(credentials)

    target = resolve_path(
        req.path,
        req.workspace,
    )

    try:
        target.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        mode = "a" if req.append else "w"

        with target.open(
            mode,
            encoding="utf-8",
        ) as file:
            file.write(req.content)

        return {
            "path": str(target),
            "bytes_written": len(
                req.content.encode("utf-8")
            ),
            "append": req.append,
            "success": True,
        }

    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Error writing file: {exc}",
        )


# =============================================================================
# Git Status
# =============================================================================

@app.post("/git/status")
async def git_status(
    req: GitStatusRequest,
    credentials: Optional[HTTPBasicCredentials] = Depends(
        security
    ),
):
    """
    Get Git status.
    """

    verify_auth(credentials)

    cwd = resolve_workspace(
        req.cwd
    )

    status_result = run_shell(
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
        "branch": branch_result["stdout"].strip(),
        "status": status_result["stdout"],
        "recent_commits": (
            log_result["stdout"]
            .strip()
            .splitlines()
            if log_result["stdout"]
            else []
        ),
        "is_git_repo": (
            status_result["exit_code"] == 0
        ),
    }


# =============================================================================
# Git Worktree
# =============================================================================

def validate_git_name(
    value: str,
    field_name: str,
) -> str:
    """
    Prevent shell metacharacters in branch/worktree names.
    """

    if not value:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} cannot be empty.",
        )

    if (
        len(value) > 255
        or "\x00" in value
        or any(
            char in value
            for char in [
                "'",
                '"',
                "`",
                ";",
                "|",
                "&",
                "$",
                "\n",
                "\r",
            ]
        )
    ):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {field_name}.",
        )

    return value


@app.post("/git/worktree")
async def git_worktree(
    req: GitWorktreeRequest,
    credentials: Optional[HTTPBasicCredentials] = Depends(
        security
    ),
):
    """
    Manage Git worktrees.
    """

    verify_auth(credentials)

    workspace = resolve_workspace(
        req.workspace
    )

    worktree_base = (
        workspace / ".worktrees"
    )

    worktree_base.mkdir(
        parents=True,
        exist_ok=True,
    )

    if req.action == "list":

        result = run_shell(
            "git worktree list --porcelain",
            cwd=str(workspace),
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
                detail="branch is required.",
            )

        if not req.path:
            raise HTTPException(
                status_code=400,
                detail="path is required.",
            )

        branch = validate_git_name(
            req.branch,
            "branch",
        )

        target_path = resolve_path(
            req.path,
            worktree_base,
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

        command = (
            "git worktree add "
            f'"{target_path}" '
            "-b "
            f'"{branch}"'
        )

        result = run_shell(
            command,
            cwd=str(workspace),
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
                detail="path is required.",
            )

        target_path = resolve_path(
            req.path,
            worktree_base,
        )

        if target_path == worktree_base:
            raise HTTPException(
                status_code=400,
                detail="Invalid worktree path.",
            )

        command = (
            "git worktree remove "
            f'"{target_path}" '
            "--force"
        )

        result = run_shell(
            command,
            cwd=str(workspace),
        )

        return {
            "action": "remove",
            "path": str(target_path),
            "result": result,
        }

    raise HTTPException(
        status_code=400,
        detail=(
            f"Unknown worktree action: "
            f"{req.action}"
        ),
    )


# =============================================================================
# LSP / Basic Symbol Query
# =============================================================================

@app.post("/lsp/query")
async def lsp_query(
    req: LspQueryRequest,
    credentials: Optional[HTTPBasicCredentials] = Depends(
        security
    ),
):
    """
    Basic source-code symbol extraction.

    This is a lightweight fallback and is not a full LSP server.
    """

    verify_auth(credentials)

    target = resolve_path(
        req.file,
        req.workspace,
    )

    if not target.exists():
        raise HTTPException(
            status_code=404,
            detail=f"File not found: {req.file}",
        )

    if not target.is_file():
        raise HTTPException(
            status_code=400,
            detail=f"Path is not a file: {req.file}",
        )

    try:
        content = target.read_text(
            encoding="utf-8",
            errors="replace",
        )

        lines = content.splitlines()

        symbols: List[Dict[str, Any]] = []

        if target.suffix.lower() in [
            ".py",
            ".pyw",
        ]:

            python_pattern = re.compile(
                r"^\s*(async\s+def|def|class)\s+([A-Za-z_]\w*)"
            )

            for line_number, line in enumerate(
                lines,
                1,
            ):
                match = python_pattern.match(
                    line
                )

                if match:
                    symbols.append(
                        {
                            "line": line_number,
                            "name": match.group(2),
                            "text": line.strip(),
                            "type": match.group(1),
                        }
                    )

        elif target.suffix.lower() in [
            ".js",
            ".jsx",
            ".ts",
            ".tsx",
        ]:

            javascript_pattern = re.compile(
                r"^\s*(?:export\s+)?"
                r"(class|function)\s+"
                r"([A-Za-z_$][\w$]*)"
            )

            variable_pattern = re.compile(
                r"^\s*(?:export\s+)?"
                r"(const|let|var)\s+"
                r"([A-Za-z_$][\w$]*)"
            )

            for line_number, line in enumerate(
                lines,
                1,
            ):

                match = (
                    javascript_pattern.match(
                        line
                    )
                )

                if match:
                    symbols.append(
                        {
                            "line": line_number,
                            "name": match.group(2),
                            "text": line.strip(),
                            "type": match.group(1),
                        }
                    )
                    continue

                match = (
                    variable_pattern.match(
                        line
                    )
                )

                if match:
                    symbols.append(
                        {
                            "line": line_number,
                            "name": match.group(2),
                            "text": line.strip(),
                            "type": match.group(1),
                        }
                    )

        if req.symbol:
            symbols = [
                item
                for item in symbols
                if item["name"] == req.symbol
            ]

        return {
            "file": str(target),
            "query_type": req.type,
            "symbol": req.symbol,
            "total_lines": len(lines),
            "symbols_found": len(symbols),
            "symbols": symbols[:100],
            "note": (
                "This endpoint provides lightweight "
                "symbol extraction. Full LSP integration "
                "requires language-specific language "
                "servers."
            ),
        }

    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"LSP query error: {exc}",
        )


# =============================================================================
# Exception Handler
# =============================================================================

@app.exception_handler(Exception)
async def unhandled_exception_handler(
    request: Request,
    exc: Exception,
):
    """
    Prevent internal exception details from being unnecessarily
    exposed to clients.
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

    uvicorn.run(
        app,
        host=OPENCODE_SERVER_HOSTNAME,
        port=OPENCODE_SERVER_PORT,
    )
