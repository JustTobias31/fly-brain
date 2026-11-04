from __future__ import annotations

import asyncio
import os
import time
from collections import defaultdict
from typing import Any

from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

app = FastAPI(title="Fly Brain Simulation API", version="0.1.0")

# The API is intended for a locally hosted simulation. Set FLY_BRAIN_CORS_ORIGINS
# when serving the frontend from a different local origin.
origins = os.getenv("FLY_BRAIN_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173,http://localhost:4173,http://127.0.0.1:4173").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in origins if origin.strip()],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Neuron-ID"],
)

clients: set[WebSocket] = set()
neurons: dict[int, dict[str, Any]] = {}
latest_state: dict[str, Any] = {"updated_at": None, "body": None, "activity": None}
event_loop: asyncio.AbstractEventLoop | None = None


class Signal(BaseModel):
    intensity: float = Field(default=1.0, ge=0.0, le=100.0)
    duration_ms: int = Field(default=100, ge=1, le=60000)
    payload: dict[str, Any] = Field(default_factory=dict)


class SensoryInput(BaseModel):
    # Sensory-only interface: accepted data is passed to the simulation's sensory
    # adapter. It is deliberately not a direct neuron stimulation endpoint.
    modality: str = Field(pattern="^(vision|olfaction|gustation|mechanosensation|temperature|other)$")
    values: dict[str, Any] = Field(default_factory=dict)
    duration_ms: int = Field(default=100, ge=1, le=60000)


async def broadcast(message: dict[str, Any]) -> int:
    stale = []
    delivered = 0
    for ws in tuple(clients):
        try:
            await ws.send_json(message)
            delivered += 1
        except Exception:
            stale.append(ws)
    for ws in stale:
        clients.discard(ws)
    return delivered


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "websocket_clients": len(clients),
        "registered_neurons": len(neurons),
        "updated_at": latest_state["updated_at"],
    }


@app.post("/reward")
async def reward(signal: Signal):
    delivered = await broadcast({
        "type": "reward",
        "intensity": signal.intensity,
        "duration_ms": signal.duration_ms,
        "payload": signal.payload,
        "timestamp": time.time(),
    })
    return {"accepted": True, "delivered_to": delivered}


@app.post("/punish")
async def punish(signal: Signal):
    delivered = await broadcast({
        "type": "punish",
        "intensity": signal.intensity,
        "duration_ms": signal.duration_ms,
        "payload": signal.payload,
        "timestamp": time.time(),
    })
    return {"accepted": True, "delivered_to": delivered}


@app.post("/input")
async def sensory_input(data: SensoryInput):
    delivered = await broadcast({
        "type": "sensory_input",
        "modality": data.modality,
        "values": data.values,
        "duration_ms": data.duration_ms,
        "timestamp": time.time(),
    })
    return {"accepted": True, "delivered_to": delivered}


@app.get("/neurons")
async def get_neurons(x_neuron_id: str | None = Header(default=None, alias="X-Neuron-ID")):
    if x_neuron_id is not None:
        try:
            neuron_id = int(x_neuron_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="X-Neuron-ID must be an integer")
        neuron = neurons.get(neuron_id)
        if neuron is None:
            raise HTTPException(status_code=404, detail=f"Neuron {neuron_id} is not registered")
        return neuron
    return {"count": len(neurons), "neurons": list(neurons.values()), "activity_updated_at": latest_state["updated_at"]}


@app.get("/state")
async def get_state():
    return latest_state


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    try:
        await ws.send_json({"type": "connected", "registered_neurons": len(neurons)})
        while True:
            message = await ws.receive_json()
            kind = message.get("type")
            if kind == "neurons:register":
                items = message.get("neurons", [])
                if not isinstance(items, list):
                    await ws.send_json({"type": "error", "error": "neurons must be an array"})
                    continue
                for item in items:
                    if not isinstance(item, dict) or not isinstance(item.get("id"), int):
                        continue
                    neurons[item["id"]] = item
                await ws.send_json({"type": "neurons:registered", "count": len(neurons)})
            elif kind == "neurons:activity":
                updates = message.get("neurons", [])
                if isinstance(updates, list):
                    for item in updates:
                        if isinstance(item, dict) and isinstance(item.get("id"), int):
                            current = neurons.setdefault(item["id"], {"id": item["id"]})
                            current["activity"] = item.get("activity")
                            current["firing_rate"] = item.get("firing_rate")
                            current["spike_count"] = item.get("spike_count")
                    latest_state["activity"] = {"count": len(updates), "timestamp": time.time()}
                    latest_state["updated_at"] = time.time()
            elif kind == "state":
                latest_state.update({
                    "body": message.get("body"),
                    "activity": message.get("activity", latest_state["activity"]),
                    "updated_at": time.time(),
                })
            elif kind == "ping":
                await ws.send_json({"type": "pong", "timestamp": time.time()})
            else:
                await ws.send_json({"type": "error", "error": f"Unknown message type: {kind}"})
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(ws)
