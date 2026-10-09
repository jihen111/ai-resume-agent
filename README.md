# AI Resume Agent

Upload a resume and a job description, and get back a structured analysis: contact details, categorised skills, work experience with business impact, a profile summary, and a **0–100 job-fit score** with a written justification.

- **Backend:** Python, FastAPI, Pydantic, LangChain + OpenAI
- **Frontend:** React 19, TypeScript, Vite, Tailwind CSS (French interface)
- **Resume formats:** PDF (text-based), TXT, Markdown, or pasted text, up to 5 MB

## How it works

```
React app (port 5173)
   │  resume file + job description
   ▼
FastAPI backend (port 8000)
   │  file → text (PDFs via pypdf)
   ▼
Analyzer ──► OpenAI model (when OPENAI_API_KEY is set)
         └─► offline demo analyzer (when it is not)
   │
   ▼
Structured, validated result → shown in the app
```

- **Structured output.** The model must answer in a strict JSON schema ([`llm_output.py`](backend/app/schemas/llm_output.py)), so every result has the same shape and is validated before it reaches the user.
- **Evidence-based scoring.** The prompt ([`openai_analyzer.py`](backend/app/services/ai/openai_analyzer.py)) has the model extract only facts stated in the resume, sort the job's requirements into must-haves and nice-to-haves, weigh how strong the evidence is for each, and write its reasoning *before* the score.
- **Swappable analyzers.** The API depends only on the `ResumeAnalyzer` interface ([`base.py`](backend/app/services/ai/base.py)), so providers can be swapped. Without an API key, the app falls back to a keyword-based [demo analyzer](backend/app/services/ai/demo_analyzer.py) so it runs end to end with no external service. Demo results are clearly labelled and are not an AI assessment.
- **Traceability.** Each analysis records the model and prompt version that produced it.

## Getting started

Requirements: **Python 3.11+** and **Node.js 20+**.

### 1. Backend

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

The API runs at http://localhost:8000. Interactive docs are at http://localhost:8000/docs.

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173.

### 3. (Optional) Enable the real AI analysis

With no key set, the app runs in **demo mode**. To use OpenAI, set the key before starting the backend:

```bash
# macOS/Linux
export OPENAI_API_KEY=sk-...
# Windows PowerShell
$env:OPENAI_API_KEY = "sk-..."
```

Optional settings: `OPENAI_MODEL` (default `gpt-4o-mini`), `OPENAI_TIMEOUT_S`, `OPENAI_MAX_RETRIES`, `MAX_UPLOAD_SIZE_MB` (default 5), and `CORS_ORIGINS`.

> Never commit your API key. `.env` is already in `.gitignore`.

### Alternative: Docker

```bash
cp .env.example .env   # then set OPENAI_API_KEY in .env
docker compose up --build
```

This starts the API, the frontend and a PostgreSQL container, with hot reload.

## API

| Method | Route | Description |
|---|---|---|
| `POST` | `/api/v1/analyses` | Analyse a resume (`multipart/form-data`: `file`, `job_description`) |
| `GET` | `/api/v1/analyses` | List analyses, most recent first |
| `GET` | `/api/v1/analyses/{id}` | Get one analysis |
| `GET` | `/api/v1/health` | Health check |

Example:

```bash
curl -X POST http://localhost:8000/api/v1/analyses \
  -F "file=@resume.pdf" \
  -F "job_description=Python backend engineer with FastAPI, PostgreSQL and Docker experience."
```

## Project structure

```
backend/
  app/
    main.py                     # app setup, CORS, error → HTTP status mapping
    api/v1/endpoints/analyses.py  # routes, file validation, PDF/text extraction
    schemas/llm_output.py       # result schema enforced on the model
    services/ai/
      base.py                   # ResumeAnalyzer interface + error types
      openai_analyzer.py        # OpenAI implementation (LangChain)
      demo_analyzer.py          # offline keyword-based fallback
frontend/
  src/App.tsx                   # upload form, results, history
  src/api.ts                    # typed API client
docker-compose.yml
```

## Limitations and roadmap

- **Storage is in memory.** Analyses are lost when the backend restarts. PostgreSQL storage (SQLAlchemy) is the next step.
- **Scanned PDFs** (image-only) are rejected; reading them would need OCR.
- **No authentication.** The app is meant for local use and demos.
