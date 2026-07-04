"""Warpspeed ticket backend.

SCAFFOLD ONLY (build step 1). The full service is implemented in build step 4:

  - SQLAlchemy async (asyncpg) models -> `tickets` table
      id (uuid PK), running number rendered as TKT-000123, client_id (indexed,
      ERP-mappable), summary, detail, priority, status (default "open"),
      source ("slack"), requester, created_at, updated_at
  - Endpoints:
      POST   /tickets
      GET    /tickets/{id}
      GET    /tickets?client_id=&status=
      PATCH  /tickets/{id}
      GET    /health
  - Writes protected by a shared-secret bearer (TICKET_API_KEY).
  - `create_all` on startup for v1; Alembic migrations noted for later.

For now this exposes only /health so the container and docker-compose health
check come up cleanly and the agent-service can be pointed at it later.
"""

from fastapi import FastAPI

app = FastAPI(title="Warpspeed Ticket Backend", version="0.1.0")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
