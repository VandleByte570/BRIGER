# Unified AI Suite

[![Docker](https://img.shields.io/badge/Docker-Ready-blue?logo=docker)](https://docker.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Open WebUI](https://img.shields.io/badge/Open%20WebUI-Integrated-green)](https://openwebui.com)
[![OpenCode](https://img.shields.io/badge/OpenCode-Integrated-orange)](https://opencode.ai)

&gt; **Production-ready, single-container AI suite** integrating **Open WebUI** (central UI), **OpenCode** (agentic coding engine), and **GodMode** (5-stage gated engineering workflow) — deployable anywhere Docker runs, including Hugging Face Spaces CPU Standard.

---

## One-Line Docker Run

```bash
docker run -d \
  -p 8080:8080 \
  -p 4096:4096 \
  -e WEBUI_SECRET_KEY="$(openssl rand -hex 32)" \
  -e OPENAI_API_KEY="sk-..." \
  -v openwebui-data:/app/data \
  -v opencode-workspace:/app/workspace \
  ghcr.io/yourusername/unified-ai-suite:latest
