# Backend API - AI Study Companion

Source-grounded AI study companion backend built with FastAPI and Python 3.12.

## Development

### Prerequisites
- Python 3.12 (managed by `uv`)
- Root `.env` file populated according to `.env.example`

### Run Development Server
```powershell
uv run --env-file ../.env uvicorn app.main:app --reload --port 8000
```

### Run Tests
```powershell
uv run --env-file ../.env pytest
```

### Run Linter
```powershell
uv run ruff check
```

## Endpoints
- `GET /v1/health` - Liveness check (`{"status": "ok"}`)
- `GET /docs` - Swagger UI documentation
- `GET /openapi.json` - OpenAPI schema definition
