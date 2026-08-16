"""
BRIGER OpenCode Headless Server

FastAPI bridge between Open WebUI and the OpenCode CLI.

Main endpoint:
    POST /tui

The /tui endpoint executes:

    opencode run --auto --dir <workspace> <prompt>

All filesystem, shell, and git operations are restricted to WORKSPACE_DIR.
"""

from __future__ import annotations

import os
import re
import secrets
import subprocess
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
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

OPENCODE_SERVER_USERNAME = os.getenv(
    "OPENCODE_SERVER_USERNAME",
    "opencode",
)

OPENCODE_SERVER_PASSWORD = os.getenv(
    "OPENCODE_SERVER_PASSWORD",
    "",
)

OPENCODE_TIMEOUT = int(
    os.getenv(
        "OPENCODE_TIMEOUT",
        "1800",
    )
)

COMMAND_TIMEOUT = int(
    os.getenv(
        "COMMAND_TIMEOUT",
        "300",
    )
)

LOG_LEVEL = os.getenv(
    "LOG_LEVEL",
    "info",
).lower()


# Quick runtime check for the opencode binary. This makes failures explicit
# and allows /tui to return a clear 503 when the binary isn't present.
OPENCODE_BINARY_AVAILABLE = shutil.which(OPENCODE_BINARY) is not None


WORKSPACE_DIR.mkdir(
    parents=True,
    exist_ok=True,
)

# Ensure opencode config directory exists so the server can run standalone
OPENCODE_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
(OPENCODE_CONFIG_DIR / "skills").mkdir(parents=True, exist_ok=True)


# =============================================================================
# Application
# =============================================================================

app = FastAPI(
    title="BRIGER OpenCode Server",
    description=(
        "Secure FastAPI bridge for OpenCode "
        "headless coding tasks."
    ),
    version="2.0.0",
)


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


security = HTTPBasic(
    auto_error=False,
)


# =============================================================================
# Models
# =============================================================================

class TuiRequest(BaseModel):
    prompt: str = Field(
        ...,
        min_length=1,
        description="Natural-language coding request",
    )

    stream: bool = Field(
        default=False,
        description="Return Server-Sent Events",
    )

    workspace: Optional[str] = Field(
        default=None,
        description="Optional workspace-relative path",
    )

    mode: str = Field(
        default="headless",
        description="Execution mode",
    )

    model: Optional[str] = Field(
        default=None,
        description="Optional OpenCode model",
    )

    agent: Optional[str] = Field(
        default=None,
        description="Optional OpenCode agent",
    )


class ExecuteRequest(BaseModel):
    type: str = Field(
        default="shell",
    )

    command: str = Field(
        ...,
        min_length=1,
    )

    cwd: Optional[str] = Field(
        default=None,
    )

    user: str = Field(
        default="unknown",
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
        le=5000,
    )

    workspace: Optional[str] = None


class FileWriteRequest(BaseModel):
    path: str

    content: str

    append: bool = False

    workspace: Optional[str] = None


class GitStatusRequest(BaseModel):
    cwd: Optional[str] = None


class GitWorktreeRequest(BaseModel):
    action: str

    branch: Optional[str] = None

    path: Optional[str] = None

    workspace: Optional[str] = None


class LspQueryRequest(BaseModel):
    file: str

    type: str = "symbols"

    symbol: Optional[str] = None

    workspace: Optional[str] = None


# =============================================================================
# Authentication
# =============================================================================

def verify_auth(
    credentials: Optional[HTTPBasicCredentials],
) -> bool:
    """
    Verify Basic Auth when a password is configured.

    If OPENCODE_SERVER_PASSWORD is empty, authentication
    is disabled.
    """

    if not OPENCODE_SERVER_PASSWORD:
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
        OPENCODE_SERVER_USERNAME,
    )

    valid_password = secrets.compare_digest(
        credentials.password,
        OPENCODE_SERVER_PASSWORD,
    )

    if not (
        valid_user
        and valid_password
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={
                "WWW-Authenticate": "Basic",
            },
        )

    return True


# =============================================================================
# Path Security
# =============================================================================

def get_workspace(
    requested: Optional[str] = None,
) -> Path:
    """
    Resolve a requested workspace.

    The resulting directory must remain inside WORKSPACE_DIR.
    """

    if not requested:
        return WORKSPACE_DIR

    candidate = Path(requested)

    if not candidate.is_absolute():
        candidate = WORKSPACE_DIR / candidate

    candidate = candidate.resolve()

    try:
        candidate.relative_to(
            WORKSPACE_DIR
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Requested workspace is outside "
                "the configured workspace."
            ),
        )

    candidate.mkdir(
        parents=True,
        exist_ok=True,
    )

    return candidate


def resolve_path(
    path: str,
    workspace: Optional[str] = None,
) -> Path:
    """
    Resolve a file path while preventing directory traversal.
    """

    root = get_workspace(
        workspace
    )

    target = Path(path)

    if not target.is_absolute():
        target = root / target

    target = target.resolve()

    try:
        target.relative_to(
            root
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Path '{path}' is outside "
                "the workspace."
            ),
        )

    return target


# =============================================================================
# Command Security
# =============================================================================

BLOCKED_COMMAND_PATTERNS = [
    r"\brm\s+-rf\s+/",
    r"\brm\s+-fr\s+/",
    r"\bmkfs(?:\.\w+)?\b",
    r"\bdd\s+if=.*\bof=/dev/",
    r">\s*/dev/",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bpoweroff\b",
    r"\bhalt\b",
    r":\(\)\s*\{\s*:\|:\s*&\s*\};:",
    r"\bcurl\b[^|]*\|\s*(?:sh|bash)\b",
    r"\bwget\b[^|]*\|\s*(?:sh|bash)\b",
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
) -> Tuple[bool, str]:

    for pattern in BLOCKED_COMMAND_REGEX:

        if pattern.search(command):

            return (
                False,
                "Command blocked by safety policy.",
            )

    return True, ""


def validate_git_name(
    value: str,
    field_name: str,
) -> str:

    if not value:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} is required.",
        )

    if len(value) > 200:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} is too long.",
        )

    if "\x00" in value:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {field_name}.",
        )

    if not re.fullmatch(
        r"[A-Za-z0-9._/\-]+",
        value,
    ):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {field_name}.",
        )

    return value


# =============================================================================
# Subprocess Helpers
# =============================================================================

def run_process(
    args: List[str],
    cwd: Path,
    timeout: int,
) -> Dict[str, Any]:

    try:

        result = subprocess.run(
            args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=os.environ.copy(),
        )

        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
            "cwd": str(cwd),
        }

    except subprocess.TimeoutExpired:

        return {
            "stdout": "",
            "stderr": (
                f"Command timed out after "
                f"{timeout} seconds."
            ),
            "exit_code": 124,
            "cwd": str(cwd),
        }

    except FileNotFoundError as exc:

        return {
            "stdout": "",
            "stderr": str(exc),
            "exit_code": 127,
            "cwd": str(cwd),
        }

    except Exception as exc:

        return {
            "stdout": "",
            "stderr": str(exc),
            "exit_code": 1,
            "cwd": str(cwd),
        }


def run_shell(
    command: str,
    cwd: Optional[str] = None,
    timeout: int = COMMAND_TIMEOUT,
) -> Dict[str, Any]:
    """
    Execute a shell command strictly inside the workspace.

    Shell execution is still inherently powerful, so this endpoint
    should only be exposed to trusted authenticated clients.
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
            check=False,
        )

        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
            "cwd": str(work_dir),
        }

    except subprocess.TimeoutExpired:

        return {
            "stdout": "",
            "stderr": (
                f"Command timed out after "
                f"{timeout} seconds."
            ),
            "exit_code": 124,
            "cwd": str(work_dir),
        }

    except Exception as exc:

        return {
            "stdout": "",
            "stderr": str(exc),
            "exit_code": 1,
            "cwd": str(work_dir),
        }


# =============================================================================
# OpenCode
# =============================================================================

def build_opencode_command(
    prompt: str,
    workspace: Path,
    model: Optional[str] = None,
    agent: Optional[str] = None,
) -> List[str]:

    command = [
        OPENCODE_BINARY,
        "run",
        "--auto",
        "--dir",
        str(workspace),
        "--format",
        "json",
    ]

    if model:
        command.extend(
            [
                "--model",
                model,
            ]
        )

    if agent:
        command.extend(
            [
                "--agent",
                agent,
            ]
        )

    command.append(
        prompt
    )

    return command


def execute_opencode(
    prompt: str,
    workspace: Path,
    model: Optional[str] = None,
    agent: Optional[str] = None,
) -> Dict[str, Any]:

    command = build_opencode_command(
        prompt=prompt,
        workspace=workspace,
        model=model,
        agent=agent,
    )

    result = run_process(
        command,
        cwd=workspace,
        timeout=OPENCODE_TIMEOUT,
    )

    result["command"] = command[
        :-1
    ]

    return result


def stream_opencode(
    prompt: str,
    workspace: Path,
    model: Optional[str] = None,
    agent: Optional[str] = None,
) -> Iterator[str]:

    command = build_opencode_command(
        prompt=prompt,
        workspace=workspace,
        model=model,
        agent=agent,
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
            env=os.environ.copy(),
        )

        if process.stdout is None:
            yield (
                "data: {\"error\":"
                "\"OpenCode produced no output.\"}\n\n"
            )
            return

        for line in process.stdout:

            line = line.rstrip("\n")

            if not line:
                continue

            yield (
                "data: "
                + _json_dumps(
                    {
                        "content": line,
                    }
                )
                + "\n\n"
            )

        return_code = process.wait(
            timeout=OPENCODE_TIMEOUT
        )

        yield (
            "data: "
            + _json_dumps(
                {
                    "done": True,
                    "exit_code": return_code,
                }
            )
            + "\n\n"
        )

    except subprocess.TimeoutExpired:

        if process is not None:

            process.kill()

            process.wait()

        yield (
            "data: "
            + _json_dumps(
                {
                    "error": (
                        "OpenCode timed out after "
                        f"{OPENCODE_TIMEOUT} seconds."
                    ),
                    "exit_code": 124,
                }
            )
            + "\n\n"
        )

    except Exception as exc:

        if process is not None:

            try:
                process.kill()
            except Exception:
                pass

        yield (
            "data: "
            + _json_dumps(
                {
                    "error": str(exc),
                    "exit_code": 1,
                }
            )
            + "\n\n"
        )


def _json_dumps(
    value: Any,
) -> str:

    import json

    return json.dumps(
        value,
        ensure_ascii=False,
    )


# =============================================================================
# Prompt
# =============================================================================

def load_skill_context() -> str:

    possible_dirs = [
        OPENCODE_CONFIG_DIR / "skills",
        Path("/app/.opencode/skills"),
        Path("/app/opencode/skills"),
    ]

    files: List[Path] = []

    for directory in possible_dirs:

        if not directory.exists():
            continue

        files.extend(
            directory.glob("*.md")
        )

        files.extend(
            directory.glob(
                "*/SKILL.md"
            )
        )

    unique_files = sorted(
        {
            path.resolve()
            for path in files
            if path.is_file()
        }
    )

    if not unique_files:
        return ""

    sections = []

    for skill_file in unique_files:

        try:

            content = skill_file.read_text(
                encoding="utf-8"
            )

            sections.append(
                f"\n--- SKILL: "
                f"{skill_file.name} ---\n"
                f"{content}"
            )

        except Exception:
            continue

    return "\n".join(
        sections
    )


def build_full_prompt(
    prompt: str,
    workspace: Path,
) -> str:

    skills = load_skill_context()

    return f"""
 You are BRIGER's OpenCode coding agent.

 WORKSPACE:
 {workspace}

 IMPORTANT RULES:

 1. Work only inside the supplied workspace.
 2. Inspect the repository before modifying files.
 3. Make minimal, targeted changes.
 4. Run relevant tests after modifications.
 5. Do not expose API keys, passwords, tokens, or other secrets.
 6. Do not delete the repository.
 7. Do not push to a remote Git repository unless explicitly requested.
 8. Do not modify files outside the workspace.
 9. Explain what you changed and what tests were run.

 If a task is ambiguous, inspect the repository and make the safest reasonable interpretation.

 {skills}

 USER REQUEST:
 {prompt}
 """.strip()


# =============================================================================
# API
# =============================================================================

@app.get("/")
async def root() -> Dict[str, str]:

    return {
        "message": "BRIGER OpenCode Server",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health")
async def health() -> Dict[str, Any]:

    return {
        "status": "healthy",
        "service": "briger-opencode-server",
        "version": "2.0.0",
        "timestamp": datetime.now(
            timezone.utc
        ).isoformat(),
        "workspace": str(
            WORKSPACE_DIR
        ),
    }


# =============================================================================
# TUI / OpenCode execution
# =============================================================================

@app.post("/tui")
async def tui_process(
    req: TuiRequest,
    credentials: Optional[
        HTTPBasicCredentials
    ] = Depends(security),
):

    verify_auth(
        credentials
    )

    # If opencode is not available, return a clear service-unavailable error.
    if not OPENCODE_BINARY_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail=(
                "opencode binary not found. "
                "Install opencode (opencode-ai / opencode-cli) in the image."
            ),
        )

    workspace = get_workspace(
        req.workspace
    )

    full_prompt = build_full_prompt(
        req.prompt,
        workspace,
    )

    if req.stream:

        return StreamingResponse(
            stream_opencode(
                prompt=full_prompt,
                workspace=workspace,
                model=req.model,
                agent=req.agent,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    result = execute_opencode(
        prompt=full_prompt,
        workspace=workspace,
        model=req.model,
        agent=req.agent,
    )

    if result["exit_code"] != 0:

        raise HTTPException(
            status_code=502,
            detail={
                "message": "OpenCode execution failed.",
                "stderr": result["stderr"],
                "stdout": result["stdout"],
                "exit_code": result["exit_code"],
            },
        )

    return {
        "content": result["stdout"],
        "stderr": result["stderr"],
        "exit_code": result["exit_code"],
        "workspace": str(workspace),
        "mode": req.mode,
        "timestamp": datetime.now(
            timezone.utc
        ).isoformat(),
    }


# =============================================================================
# Shell
# =============================================================================

@app.post("/execute")
async def execute_command(
    req: ExecuteRequest,
    credentials: Optional[
        HTTPBasicCredentials
    ] = Depends(security),
):

    verify_auth(
        credentials
    )

    return run_shell(
        command=req.command,
        cwd=req.cwd,
    )


# =============================================================================
# Files
# =============================================================================

@app.post("/file/read")
async def file_read(
    req: FileReadRequest,
    credentials: Optional[
        HTTPBasicCredentials
    ] = Depends(security),
):

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

    except OSError as exc:

        raise HTTPException(
            status_code=500,
            detail=str(exc),
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


@app.post("/file/write")
async def file_write(
    req: FileWriteRequest,
    credentials: Optional[
        HTTPBasicCredentials
    ] = Depends(security),
):

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

    except OSError as exc:

        raise HTTPException(
            status_code=500,
            detail=str(exc),
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


# =============================================================================
# Git
# =============================================================================

@app.post("/git/status")
async def git_status(
    req: GitStatusRequest,
    credentials: Optional[
        HTTPBasicCredentials
    ] = Depends(security),
):

    verify_auth(
        credentials
    )

    cwd = get_workspace(
        req.cwd
    )

    status_result = run_process(
        [
            "git",
            "status",
            "--short",
            "--branch",
        ],
        cwd=cwd,
        timeout=60,
    )

    log_result = run_process(
        [
            "git",
            "log",
            "--oneline",
            "-5",
        ],
        cwd=cwd,
        timeout=60,
    )

    branch_result = run_process(
        [
            "git",
            "branch",
            "--show-current",
        ],
        cwd=cwd,
        timeout=60,
    )

    return {
        "cwd": str(cwd),
        "branch": branch_result[
            "stdout"
        ].strip(),
        "status": status_result[
            "stdout"
        ],
        "recent_commits": (
            log_result["stdout"]
            .strip()
            .splitlines()
            if log_result["stdout"].strip()
            else []
        ),
        "is_git_repo": (
            status_result[
                "exit_code"
            ] == 0
        ),
    }


@app.post("/git/worktree")
async def git_worktree(
    req: GitWorktreeRequest,
    credentials: Optional[
        HTTPBasicCredentials
    ] = Depends(security),
):

    verify_auth(
        credentials
    )

    workspace = get_workspace(
        req.workspace
    )

    worktree_base = (
        workspace / ".worktrees"
    )

    worktree_base.mkdir(
        parents=True,
        exist_ok=True,
    )

    action = req.action.lower()

    if action == "list":

        result = run_process(
            [
                "git",
                "worktree",
                "list",
                "--porcelain",
            ],
            cwd=workspace,
            timeout=60,
        )

        return {
            "action": "list",
            "worktrees": result[
                "stdout"
            ],
            "stderr": result[
                "stderr"
            ],
            "exit_code": result[
                "exit_code"
            ],
        }

    if action == "create":

        branch = validate_git_name(
            req.branch or "",
            "branch",
        )

        path_name = validate_git_name(
            req.path or "",
            "path",
        )

        target_path = (
            worktree_base
            / path_name
        ).resolve()

        try:
            target_path.relative_to(
                worktree_base.resolve()
            )
        except ValueError:

            raise HTTPException(
                status_code=403,
                detail="Invalid worktree path.",
            )

        if target_path.exists():

            raise HTTPException(
                status_code=409,
                detail=(
                    "Worktree path already "
                    "exists."
                ),
            )

        result = run_process(
            [
                "git",
                "worktree",
                "add",
                str(target_path),
                "-b",
                branch,
            ],
            cwd=workspace,
            timeout=120,
        )

        return {
            "action": "create",
            "branch": branch,
            "path": str(target_path),
            "result": result,
        }

    if action == "remove":

        path_name = validate_git_name(
            req.path or "",
            "path",
        )

        target_path = (
            worktree_base
            / path_name
        ).resolve()

        try:
            target_path.relative_to(
                worktree_base.resolve()
            )
        except ValueError:

            raise HTTPException(
                status_code=403,
                detail="Invalid worktree path.",
            )

        result = run_process(
            [
                "git",
                "worktree",
                "remove",
                str(target_path),
                "--force",
            ],
            cwd=workspace,
            timeout=120,
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
# LSP-like fallback
# =============================================================================

@app.post("/lsp/query")
async def lsp_query(
    req: LspQueryRequest,
    credentials: Optional[
        HTTPBasicCredentials
    ] = Depends(security),
):

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
            detail="Path is not a file.",
        )

    content = target.read_text(
        encoding="utf-8",
        errors="replace",
    )

    lines = content.splitlines()

    symbols: List[
        Dict[str, Any]
    ] = []

    if target.suffix in (
        ".py",
        ".pyw",
    ):

        pattern = re.compile(
            r"^\s*"
            r"(async\s+def|def|class)"
            r"\s+([A-Za-z_]\w*)"
        )

        for number, line in enumerate(
            lines,
            start=1,
        ):

            match = pattern.match(
                line
            )

            if match:

                symbols.append(
                    {
                        "line": number,
                        "name": match.group(2),
                        "text": line.strip(),
                        "type": match.group(1),
                    }
                )

    elif target.suffix in (
        ".js",
        ".ts",
        ".jsx",
        ".tsx",
    ):

        pattern = re.compile(
            r"^\s*"
            r"(?:export\s+)?"
            r"(?:async\s+)?"
            r"(function|class)"
            r"\s+([A-Za-z_$][\w$]*)"
        )

        for number, line in enumerate(
            lines,
            start=1,
        ):

            match = pattern.match(
                line
            )

            if match:

                symbols.append(
                    {
                        "line": number,
                        "name": match.group(2),
                        "text": line.strip(),
                        "type": match.group(1),
                    }
                )

    if req.symbol:

        symbols = [
            symbol
            for symbol in symbols
            if symbol.get(
                "name"
            ) == req.symbol
        ]

    return {
        "file": str(target),
        "query_type": req.type,
        "symbol": req.symbol,
        "total_lines": len(lines),
        "symbols_found": len(symbols),
        "symbols": symbols[:100],
        "note": (
            "Fallback symbol parser. "
            "Install language-specific "
            "LSP servers for full LSP support."
        ),
    }


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":

    import uvicorn

    host = os.getenv(
        "OPENCODE_SERVER_HOSTNAME",
        "0.0.0.0",
    )

    port = int(
        os.getenv(
            "OPENCODE_SERVER_PORT",
            "4096",
        )
    )

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=LOG_LEVEL,
    )
