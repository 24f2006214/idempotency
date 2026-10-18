import time
import uuid
from collections import defaultdict, deque
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware


TOTAL_ORDERS = 51
RATE_LIMIT = 17
WINDOW_SECONDS = 10.0

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Retry-After"],
)

idempotent_orders: dict[str, dict[str, Any]] = {}
request_times: dict[str, deque[float]] = defaultdict(deque)


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)

    client_id = request.headers.get("X-Client-Id", "anonymous")
    now = time.monotonic()
    bucket = request_times[client_id]
    while bucket and now - bucket[0] >= WINDOW_SECONDS:
        bucket.popleft()

    if len(bucket) >= RATE_LIMIT:
        retry_after = max(1, int(WINDOW_SECONDS - (now - bucket[0]) + 0.999))
        return Response(
            content='{"detail":"rate limit exceeded"}',
            status_code=429,
            media_type="application/json",
            headers={"Retry-After": str(retry_after)},
        )

    bucket.append(now)
    return await call_next(request)


@app.post("/orders", status_code=201)
async def create_order(
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")

    if idempotency_key in idempotent_orders:
        return idempotent_orders[idempotency_key]

    try:
        body = await request.json()
    except Exception:
        body = {}

    order = {"id": str(uuid.uuid4())}
    if isinstance(body, dict):
        order.update(body)
        order["id"] = order["id"] if isinstance(order["id"], str) else str(order["id"])
    idempotent_orders[idempotency_key] = order
    return order


@app.get("/orders")
async def list_orders(
    limit: int = Query(10, ge=1, le=100),
    cursor: str | None = Query(default=None),
):
    start = 0
    if cursor:
        if not cursor.startswith("cursor-"):
            raise HTTPException(status_code=400, detail="invalid cursor")
        try:
            start = int(cursor.removeprefix("cursor-"))
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid cursor")

    if start < 0 or start > TOTAL_ORDERS:
        raise HTTPException(status_code=400, detail="invalid cursor")

    end = min(start + limit, TOTAL_ORDERS)
    items = [{"id": number} for number in range(start + 1, end + 1)]
    next_cursor = f"cursor-{end}" if end < TOTAL_ORDERS else None
    return {"items": items, "next_cursor": next_cursor}

