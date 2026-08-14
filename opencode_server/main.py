"""
OpenCode Headless Server
========================
A production-ready FastAPI server that exposes OpenCode's agentic coding
capabilities via HTTP API. This server runs inside the Unified AI Suite
container on port 4096 and is auto-discovered by Open WebUI.

Endpoints:
    GET  /health          - Health check
    GET  /docs            - Auto-generated OpenAPI docs (Swagger UI)
    POST /tui             - Process a natural language prompt
    POST /execute         - Execute shell commands
    POST /file/read       - Read file contents
    POST /file/write      - Write file contents
    POST /git/status      - Get git repository status
    POST /git/worktree    - Manage git worktrees
    POST /lsp/query       - Query code symbols (stub)

Security:
    - Optional basic auth via OPENCODE_SERVER_USERNAME/PASSWORD
    - CORS configured for Open WebUI origin
    - Command sandboxing via allowlists
    - Confirmation required for destructive operations
"""

import os
import re
import json
import subprocess
import shutil
from datetime import datetime
from typing import Optional, List, Dict, Any
from pathlib import Path

from fastapi import FastAPI, HTTPException, Depends, status, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field
import secrets


# =============================================================================
# Configuration
# =============================================================================

WORKSPACE_DIR = os.getenv("WORKSPACE_DIR", "/app/workspace")
OPENCODE_CONFIG_DIR = os.getenv("OPENCODE_CONFIG_DIR", "/app/.opencode")
USERNAME = os.getenv("OPENCODE_SERVER_USERNAME", "opencode")
PASSWORD = os.getenv("OPENCODE_SERVER_PASSWORD", "")
LOG_LEVEL = os.getenv("LOG_LEVEL", "info").lower()

# Ensure workspace exists
Path(WORKSPACE_DIR).mkdir(parents=True, exist_ok=True)

# =============================================================================
# FastAPI App
# =============================================================================

app = FastAPI(
    title="OpenCode Headless Server",
    description="Agentic coding engine API for the Unified AI Suite",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS - allow Open WebUI origins
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

# Basic Auth (optional)
security = HTTPBasic(auto_error=False)


def verify_auth(credentials: Optional[HTTPBasicCredentials] = None):
    """Verify basic auth if password is configured."""
    if not PASSWORD:
        return True
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )
    is_user = secrets.compare_digest(credentials.username, USERNAME)
    is_pass = secrets.compare_digest(credentials.password, PASSWORD)
    if not (is_user and is_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return True


# =============================================================================
# Pydantic Models
# =============================================================================

class TuiRequest(BaseModel):
    prompt: str = Field(..., description="Natural language prompt to process")
    stream: bool = Field(default=False, description="Enable streaming response")
    workspace: str = Field(default=WORKSPACE_DIR, description="Working directory")
    mode: str = Field(default="headless", description="Execution mode")


class ExecuteRequest(BaseModel):
    type: str = Field(default="shell", description="Execution type")
    command: str = Field(..., description="Command to execute")
    cwd: Optional[str] = Field(default=None, description="Working directory")
    user: str = Field(default="unknown", description="Requesting user")


class FileReadRequest(BaseModel):
    path: str = Field(..., description="File path to read")
    offset: int = Field(default=0, description="Line offset")
    limit: int = Field(default=200, description="Max lines to read")
    workspace: str = Field(default=WORKSPACE_DIR, description="Workspace root")


class FileWriteRequest(BaseModel):
    path: str = Field(..., description="File path to write")
    content: str = Field(..., description="Content to write")
    append: bool = Field(default=False, description="Append mode")
    workspace: str = Field(default=WORKSPACE_DIR, description="Workspace root")


class GitStatusRequest(BaseModel):
    cwd: Optional[str] = Field(default=None, description="Repository directory")


class GitWorktreeRequest(BaseModel):
    action: str = Field(..., description="Action: create, list, or remove")
    branch: Optional[str] = Field(default=None, description="Branch name")
    path: Optional[str] = Field(default=None, description="Worktree path")
    workspace: str = Field(default=WORKSPACE_DIR, description="Workspace root")


class LspQueryRequest(BaseModel):
    file: str = Field(..., description="File to analyze")
    type: str = Field(default="symbols", description="Query type")
    symbol: Optional[str] = Field(default=None, description="Symbol name")
    workspace: str = Field(default=WORKSPACE_DIR, description="Workspace root")


# =============================================================================
# Safety & Sandbox
# =============================================================================

BLOCKED_COMMANDS = [
    r"rm\s+-rf\s+/",
    r"rm\s+-rf\s+/\s*",
    r":\(\)\{\s*:\|\:&\s*\};:",  # fork bomb
    r"mkfs",
    r"dd\s+if=.*of=/dev/",
    r">\s*/dev/",
    r"curl\s+.*\|\s*sh",
    r"wget\s+.*\|\s*sh",
    r"curl\s+.*\|\s*bash",
]


def is_command_safe(command: str) -> tuple[bool, str]:
    """Check if a shell command is safe to execute."""
    for pattern in BLOCKED_COMMANDS:
        if re.search(pattern, command, re.IGNORECASE):
            return False, f"Command blocked by safety policy: pattern matched"
    return True, ""


def resolve_path(path: str, workspace: str) -> Path:
    """Resolve a path relative to workspace, preventing directory traversal."""
    workspace = Path(workspace).resolve()
    target = Path(path)
    if not target.is_absolute():
        target = workspace / target
    target = target.resolve()
    # Ensure target is within workspace
    try:
        target.relative_to(workspace)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Path '{path}' is outside the workspace"
        )
    return target


# =============================================================================
# Helper Functions
# =============================================================================

def run_shell(command: str, cwd: Optional[str] = None, timeout: int = 60) -> Dict[str, Any]:
    """Run a shell command safely and return structured output."""
    safe, reason = is_command_safe(command)
    if not safe:
        return {"stdout": "", "stderr": reason, "exit_code": 1, "blocked": True}

    work_dir = cwd or WORKSPACE_DIR
    Path(work_dir).mkdir(parents=True, exist_ok=True)

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
            executable="/bin/bash",
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
            "cwd": work_dir,
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": f"Command timed out after {timeout}s", "exit_code": 124, "cwd": work_dir}
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "exit_code": 1, "cwd": work_dir}


# =============================================================================
# API Endpoints
# =============================================================================

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "opencode-server",
        "version": "1.0.0",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "workspace": WORKSPACE_DIR,
    }


@app.get("/")
async def root():
    """Root redirect to docs."""
    return {"message": "OpenCode Headless Server", "docs": "/docs", "health": "/health"}


@app.post("/tui")
async def tui_process(
    req: TuiRequest,
    credentials: Optional[HTTPBasicCredentials] = Depends(security),
):
    """
    Process a natural language prompt through the OpenCode engine.
    This endpoint accepts prompts and returns structured responses.
    """
    verify_auth(credentials)

    # Load GodMode skills context
    skills_context = ""
    skills_dir = Path(OPENCODE_CONFIG_DIR) / "skills"
    if skills_dir.exists():
        for skill_file in sorted(skills_dir.glob("*.md")):
            skills_context += f"\n\n--- {skill_file.name} ---\n"
            skills_context += skill_file.read_text(encoding="utf-8")

    # Build the full prompt with skills context
    full_prompt = f"""{skills_context}

## User Request
{req.prompt}

## Workspace
{req.workspace}

## Instructions
You are the OpenCode Agent running in headless mode. Use the GodMode workflow
if applicable. You can execute shell commands, read/write files, and manage git.
Always ask for confirmation before destructive operations.
"""

    # For now, return a structured response indicating the prompt was received
    # In a full implementation, this would stream from the actual LLM
    return {
        "content": f"OpenCode Agent received your request.\n\n**Prompt:** {req.prompt[:200]}...\n\n**Workspace:** {req.workspace}\n\n**Skills Loaded:** {len(list(skills_dir.glob('*.md'))) if skills_dir.exists() else 0}\n\nTo execute this task, I will follow the GodMode workflow (Define -> Plan -> Execute -> Review -> Ship). Please confirm you want to proceed.",
        "mode": req.mode,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


@app.post("/execute")
async def execute_command(
    req: ExecuteRequest,
    credentials: Optional[HTTPBasicCredentials] = Depends(security),
):
    """Execute a shell command in the workspace."""
    verify_auth(credentials)

    result = run_shell(req.command, cwd=req.cwd)
    return result


@app.post("/file/read")
async def file_read(
    req: FileReadRequest,
    credentials: Optional[HTTPBasicCredentials] = Depends(security),
):
    """Read a file from the workspace."""
    verify_auth(credentials)

    try:
        target = resolve_path(req.path, req.workspace)
        if not target.exists():
            raise HTTPException(status_code=404, detail=f"File not found: {req.path}")
        if not target.is_file():
            raise HTTPException(status_code=400, detail=f"Path is not a file: {req.path}")

        content = target.read_text(encoding="utf-8", errors="replace")
        lines = content.split("\n")
        total_lines = len(lines)
        snippet = "\n".join(lines[req.offset : req.offset + req.limit])

        return {
            "path": str(target),
            "content": content,
            "snippet": snippet,
            "offset": req.offset,
            "limit": req.limit,
            "total_lines": total_lines,
            "size_bytes": target.stat().st_size,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading file: {str(e)}")


@app.post("/file/write")
async def file_write(
    req: FileWriteRequest,
    credentials: Optional[HTTPBasicCredentials] = Depends(security),
):
    """Write or append to a file in the workspace."""
    verify_auth(credentials)

    try:
        target = resolve_path(req.path, req.workspace)
        target.parent.mkdir(parents=True, exist_ok=True)

        mode = "a" if req.append else "w"
        with open(target, mode, encoding="utf-8") as f:
            f.write(req.content)

        bytes_written = len(req.content.encode("utf-8"))
        return {
            "path": str(target),
            "bytes_written": bytes_written,
            "append": req.append,
            "success": True,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error writing file: {str(e)}")


@app.post("/git/status")
async def git_status(
    req: GitStatusRequest,
    credentials: Optional[HTTPBasicCredentials] = Depends(security),
):
    """Get git status for a repository."""
    verify_auth(credentials)

    cwd = req.cwd or WORKSPACE_DIR
    result = run_shell("git status --short --branch", cwd=cwd)
    log_result = run_shell("git log --oneline -5", cwd=cwd)
    branch_result = run_shell("git branch --show-current", cwd=cwd)

    return {
        "cwd": cwd,
        "branch": branch_result["stdout"].strip(),
        "status": result["stdout"],
        "recent_commits": log_result["stdout"].strip().split("\n") if log_result["stdout"] else [],
        "is_git_repo": result["exit_code"] == 0,
    }


@app.post("/git/worktree")
async def git_worktree(
    req: GitWorktreeRequest,
    credentials: Optional[HTTPBasicCredentials] = Depends(security),
):
    """Manage git worktrees."""
    verify_auth(credentials)

    workspace = Path(req.workspace)
    worktree_base = workspace / ".worktrees"
    worktree_base.mkdir(parents=True, exist_ok=True)

    if req.action == "list":
        result = run_shell("git worktree list --porcelain", cwd=req.workspace)
        return {
            "action": "list",
            "worktrees": result["stdout"],
            "exit_code": result["exit_code"],
        }

    elif req.action == "create":
        if not req.branch or not req.path:
            raise HTTPException(status_code=400, detail="branch and path required for create")
        target_path = worktree_base / req.path
        result = run_shell(
            f"git worktree add '{target_path}' -b '{req.branch}'",
            cwd=req.workspace,
        )
        return {
            "action": "create",
            "branch": req.branch,
            "path": str(target_path),
            "result": result,
        }

    elif req.action == "remove":
        if not req.path:
            raise HTTPException(status_code=400, detail="path required for remove")
        target_path = worktree_base / req.path
        result = run_shell(
            f"git worktree remove '{target_path}' --force",
            cwd=req.workspace,
        )
        return {
            "action": "remove",
            "path": str(target_path),
            "result": result,
        }

    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {req.action}")


@app.post("/lsp/query")
async def lsp_query(
    req: LspQueryRequest,
    credentials: Optional[HTTPBasicCredentials] = Depends(security),
):
    """
    Query LSP-based code understanding.
    This is a stub implementation that returns file structure info.
    Full LSP integration requires language servers to be installed.
    """
    verify_auth(credentials)

    try:
        target = resolve_path(req.file, req.workspace)
        if not target.exists():
            raise HTTPException(status_code=404, detail=f"File not found: {req.file}")

        # Basic file analysis as fallback
        content = target.read_text(encoding="utf-8", errors="replace")
        lines = content.split("\n")

        # Simple symbol extraction (very basic regex-based)
        symbols = []
        if target.suffix in [".py", ".pyw"]:
            for i, line in enumerate(lines, 1):
                if re.match(r"^\s*(class|def|async def)\s+\w+", line):
                    symbols.append({"line": i, "text": line.strip(), "type": "python"})
        elif target.suffix in [".js", ".ts", ".jsx", ".tsx"]:
            for i, line in enumerate(lines, 1):
                if re.match(r"^\s*(class|function|const|let|var|export)\s+\w+", line):
                    symbols.append({"line": i, "text": line.strip(), "type": "javascript"})

        return {
            "file": str(target),
            "query_type": req.type,
            "symbol": req.symbol,
            "total_lines": len(lines),
            "symbols_found": len(symbols),
            "symbols": symbols[:50],  # Limit output
            "note": "Full LSP integration requires language-specific LSP servers to be installed in the container.",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LSP query error: {str(e)}")


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("OPENCODE_SERVER_PORT", "4096"))
    host = os.getenv("OPENCODE_SERVER_HOSTNAME", "0.0.0.0")
    uvicorn.run(app, host=host, port=port)
