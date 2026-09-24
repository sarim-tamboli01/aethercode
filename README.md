# AetherCode

Autonomous multi-agent software engineering system. You give it a natural-language task and a GitHub repo; specialized agents plan, retrieve code (Multi-RAG), implement, test, debug, review security, and open a pull request. Nothing is merged automatically — a human reviews the PR on GitHub.

**Stack:** Python · FastAPI · LangGraph · Supabase (Postgres + pgvector) · Docker · GitHub API · Groq / Gemini / Ollama · React (Vite)

```
multi_agent_sys/
├── aethercode/     # FastAPI + LangGraph backend
└── frontend/       # React dashboard (live agent trace)
```

## Prerequisites

- Python 3.11+
- Node.js 18+ and npm
- Docker Desktop (for sandboxed tests)
- A [Supabase](https://supabase.com) project with the `pgvector` extension
- GitHub personal access token (repo + pull request permissions)
- At least one LLM key: Groq, Gemini, or a local Ollama server

## Local setup

```powershell
# 1. Backend
cd aethercode
copy .env.example .env
# Edit .env with your keys
cd ..
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r aethercode\requirements.txt
cd aethercode
..\venv\Scripts\uvicorn.exe main:app --host 127.0.0.1 --port 8000 --reload
```

```powershell
# 2. Frontend (second terminal)
cd frontend
npm install
npm run dev
```

Open **http://127.0.0.1:5173**. The Vite proxy forwards `/health` and `/run-task` to the API on port 8000.



The pipeline uses Docker and GitHub from the machine that runs the backend, so that host must have Docker available and a valid `GITHUB_TOKEN`.
