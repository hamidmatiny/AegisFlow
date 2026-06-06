# AegisFlow

Autonomous multi-agent AI incident response engine built with Temporal and PydanticAI.

## Phase 1: Foundation

### Local infrastructure

```bash
docker compose up -d
```

| Service      | Endpoint              |
|--------------|-----------------------|
| Temporal UI  | http://localhost:8080 |
| Temporal gRPC| localhost:7233        |
| PostgreSQL   | localhost:5432        |
| LocalStack   | http://localhost:4566 |

### Development setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
ruff check src
mypy src
```
