import asyncio, time
from collections import deque
from threading import Lock
import httpx
from fastapi import FastAPI, HTTPException

app = FastAPI()
N = 100
MIX = 37
STEP = 10
CONTROL_INTERVAL = 600

CHEAP_MAX_CONCURRENCY = 2

MAX_ERR_RATE = 0.05
MAX_P95_MS = 250
cnt = 0
lock = Lock()

cheap_target = 0
cheap_sem = asyncio.Semaphore(CHEAP_MAX_CONCURRENCY)

cheap_events = deque(maxlen=500)

def now_ms() -> int:
    return int(time.time() * 1000)

def record_cheap(ok: bool, ms: int):
    cheap_events.append((ok, ms, now_ms()))

def cheap_health(last_sec: int = 60):
    cut = now_ms() - last_sec * 1000
    data = [(ok, ms) for (ok, ms, ts) in cheap_events if ts >= cut]
    if len(data) < 20:
        return {"count": len(data), "err_rate": 0.0, "p95_ms": 0, "healthy": True}

    oks = [1 if ok else 0 for ok, _ in data]
    lats = sorted([ms for _, ms in data])
    count = len(data)
    err = 1.0 - (sum(oks) / count)
    p95 = lats[int(0.95 * (count - 1))]

    healthy = (err <= MAX_ERR_RATE) and (p95 <= MAX_P95_MS)
    return {"count": count, "err_rate": round(err, 4), "p95_ms": p95, "healthy": healthy}

def choose_backend(bucket: int, cheap_share: int):
    mixed = (bucket * MIX) % N
    chosen = "cheap" if mixed < cheap_share else "expensive"
    return chosen, mixed

@app.get("/backend/expensive")
async def expensive():
    await asyncio.sleep(0.25)
    return {"backend": "expensive"}

@app.get("/backend/cheap")
async def cheap():
    t0 = time.time()

    try:
        await asyncio.wait_for(cheap_sem.acquire(), timeout=0.0)
    except asyncio.TimeoutError:
        ms = int((time.time() - t0) * 1000)
        record_cheap(False, ms)
        raise HTTPException(status_code=503, detail="cheap overloaded")

    try:
        await asyncio.sleep(0.06)
        ms = int((time.time() - t0) * 1000)
        record_cheap(True, ms)
        return {"backend": "cheap"}
    finally:
        cheap_sem.release()
@app.get("/proxy")
async def proxy():
    global cnt, cheap_target

    await asyncio.sleep(0.03)

    with lock:
        bucket = cnt % N
        cnt += 1
        cur_cheap = cheap_target

    chosen, mixed = choose_backend(bucket, cur_cheap)
    url = f"http://127.0.0.1:8000/backend/{chosen}"

    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            r = await c.get(url)
            r.raise_for_status()
            data = r.json()
    except Exception:
        data = {"error": "upstream_failed", "chosen": chosen}
    ms = int((time.time() - t0) * 1000)
    return {
        "bucket": bucket,
        "mixed": mixed,
        "cheap_target_percent": cur_cheap,
        "chosen": chosen,
        "proxy_ms": ms,
        "resp": data,
    }

async def control_loop():
    global cheap_target
    while True:
        await asyncio.sleep(CONTROL_INTERVAL)
        h = cheap_health(last_sec=60)

        if h["healthy"]:
            cheap_target = min(100, cheap_target + STEP)
        else:
            cheap_target = max(0, cheap_target - STEP)

@app.on_event("startup")
async def startup():
   asyncio.create_task(control_loop())

@app.get("/stats")
async def stats():
    h = cheap_health(last_sec=60)
    return {
        "cheap_target_percent": cheap_target,
        "cheap_capacity": {"max_concurrency": CHEAP_MAX_CONCURRENCY},
        "cheap_health_60s": h,
        "rates": {"max_err_rate": MAX_ERR_RATE, "max_p95_ms": MAX_P95_MS}
    }
