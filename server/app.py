from __future__ import annotations

import asyncio
import base64
import binascii
import math
import os
import time
import uuid
from collections import defaultdict
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
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
active_neuron_ids: set[int] = set()
latest_state: dict[str, Any] = {"updated_at": None, "body": None, "activity": None}
event_loop: asyncio.AbstractEventLoop | None = None
sessions: dict[str, dict[str, Any]] = {}


@app.post("/sessions")
async def create_session():
    session_id = uuid.uuid4().hex[:16]
    sessions[session_id] = {"owner": None, "controllers": set(), "neurons": {}, "active_neuron_ids": set(), "state": {"updated_at": None, "body": None, "activity": None}, "simulation_status": "stopped", "created_at": time.time()}
    return {"session_id": session_id, "websocket_path": f"/ws/{session_id}", "controller_url": f"/ws/{session_id}"}


@app.delete("/sessions/{session_id}")
async def close_session(session_id: str):
    session = sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    owner = session.get("owner")
    if owner is not None:
        try:
            await owner.send_json({"type": "session:close"})
            await owner.close()
        except Exception:
            pass
    for ws in tuple(session["controllers"]):
        try:
            await ws.send_json({"type": "session:offline"})
            await ws.close()
        except Exception:
            pass
    sessions.pop(session_id, None)
    return {"closed": True, "session_id": session_id}


@app.get("/sessions/{session_id}")
async def get_session(session_id: str):
    session = sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session_id": session_id, "owner_connected": session["owner"] is not None, "controller_clients": len(session["controllers"]), "registered_neurons": len(session["neurons"]), "updated_at": session["state"]["updated_at"]}


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


async def broadcast(message: dict[str, Any], exclude: WebSocket | None = None) -> int:
    stale = []
    delivered = 0
    for ws in tuple(clients):
        if ws is exclude:
            continue
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
        "active_sessions": len(sessions),
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
async def get_neurons(x_neuron_id: str | None = Header(default=None, alias="X-Neuron-ID"), offset: int = Query(default=0, ge=0), limit: int = Query(default=1000, ge=1, le=10000)):
    if x_neuron_id is not None:
        try:
            neuron_id = int(x_neuron_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="X-Neuron-ID must be an integer")
        neuron = neurons.get(neuron_id)
        if neuron is None:
            raise HTTPException(status_code=404, detail=f"Neuron {neuron_id} is not registered")
        return neuron
    items = list(neurons.values())
    return {"count": len(items), "offset": offset, "limit": limit, "neurons": items[offset:offset + limit], "activity_updated_at": latest_state["updated_at"]}


@app.get("/state")
async def get_state():
    return latest_state


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await handle_websocket(ws)


async def handle_websocket(ws: WebSocket, session_id: str | None = None, role: str = "peer"):
    session = sessions.get(session_id) if session_id else None
    if session_id and session is None:
        await ws.close(code=1008, reason="Unknown session")
        return
    await ws.accept()
    if session is None:
        clients.add(ws)
        neurons = globals()["neurons"]
        active_neuron_ids = globals()["active_neuron_ids"]
        latest_state = globals()["latest_state"]
    else:
        if role not in {"owner", "controller"}:
            await ws.close(code=1008, reason="Role must be owner or controller")
            return
        if role == "owner":
            if session["owner"] is not None:
                await ws.close(code=1008, reason="Session already has an owner")
                return
            session["owner"] = ws
        else:
            session["controllers"].add(ws)
        neurons = session["neurons"]
        active_neuron_ids = session["active_neuron_ids"]
        latest_state = session["state"]
    async def route(message: dict[str, Any], exclude: WebSocket | None = None) -> int:
        if session is None:
            return await broadcast(message, exclude)
        targets = (tuple(session["controllers"]) if role == "owner" else ((session["owner"],) if session["owner"] is not None else ()))
        delivered = 0
        stale = []
        for target in targets:
            if target is None or target is exclude:
                continue
            try:
                # Never let a slow controller stall the simulation's activity loop.
                await asyncio.wait_for(target.send_json(message), timeout=1.0)
                delivered += 1
            except (asyncio.TimeoutError, Exception):
                stale.append(target)
                try:
                    await asyncio.wait_for(target.close(code=1013, reason="Client too slow or disconnected"), timeout=0.5)
                except Exception:
                    pass
        for target in stale:
            session["controllers"].discard(target)
            if session.get("owner") is target:
                session["owner"] = None
        return delivered
    vision_last_frame = 0.0
    try:
        await ws.send_json({"type": "connected", "session_id": session_id, "role": role, "registered_neurons": len(neurons), "state": latest_state, "simulation_status": session.get("simulation_status", "stopped") if session else None})
        if session is not None and role == "controller" and neurons:
            metadata = list(neurons.values())
            for start in range(0, len(metadata), 500):
                await ws.send_json({"type": "neurons:metadata", "neurons": metadata[start:start + 500]})
        while True:
            message = await ws.receive_json()
            kind = message.get("type")
            if session is not None:
                owner_messages = {"neurons:register", "neurons:activity", "state", "vision:frame", "vision:stop", "vision:eyes", "simulation:status", "ack", "error", "arena:list:result", "neurons:read:result", "fly:transform:result", "circuit:list:result", "circuit:read:result"}
                controller_commands = {"simulation:control", "element:command", "property:set", "arena:list", "loom:trigger", "fly:takeoff", "fly:transform:get", "scaffold:set", "vision:frame", "vision:stop", "neurons:read", "circuit:list", "circuit:read"}
                if (role == "owner" and kind not in owner_messages and kind != "ping") or (role == "controller" and kind in owner_messages and kind not in {"vision:frame", "vision:stop"} and kind not in controller_commands):
                    await ws.send_json({"type": "error", "error": f"Message type {kind} is not allowed for role {role}", "requestId": message.get("requestId")})
                    continue
            if kind == "neurons:register":
                items = message.get("neurons", [])
                if not isinstance(items, list):
                    await ws.send_json({"type": "error", "error": "neurons must be an array"})
                    continue
                valid_items = []
                for item in items:
                    if not isinstance(item, dict) or not isinstance(item.get("id"), int):
                        continue
                    neurons[item["id"]] = item
                    valid_items.append(item)
                await ws.send_json({"type": "neurons:registered", "count": len(neurons)})
                if session is not None and role == "owner" and valid_items:
                    stale = []
                    for controller in tuple(session["controllers"]):
                        try:
                            await controller.send_json({"type": "neurons:metadata", "neurons": valid_items})
                        except Exception:
                            stale.append(controller)
                    for controller in stale:
                        session["controllers"].discard(controller)
            elif kind == "neurons:activity":
                updates = message.get("neurons", [])
                if isinstance(updates, list):
                    for neuron_id in active_neuron_ids:
                        if neuron_id in neurons:
                            neurons[neuron_id]["activity"] = 0.0
                    active_neuron_ids.clear()
                    for item in updates:
                        if isinstance(item, dict) and isinstance(item.get("id"), int):
                            current = neurons.setdefault(item["id"], {"id": item["id"]})
                            current["activity"] = item.get("activity", 0.0)
                            current["firing_rate"] = item.get("firing_rate")
                            current["spike_count"] = item.get("spike_count")
                            if current["activity"]:
                                active_neuron_ids.add(item["id"])
                    updates = [{**neurons.get(item.get("id"), {}), **item} for item in updates if isinstance(item, dict)]
                    timestamp = time.time()
                    latest_state["activity"] = {"active_count": len(active_neuron_ids), "sampled_count": len(updates), "timestamp": timestamp}
                    latest_state["updated_at"] = timestamp
                    await route({"type": "neurons:activity", "neurons": updates, "activity": latest_state["activity"], "timestamp": timestamp}, exclude=ws)
            elif kind == "state":
                timestamp = time.time()
                latest_state.update({
                    "body": message.get("body"),
                    "activity": message.get("activity", latest_state["activity"]),
                    "updated_at": timestamp,
                })
                await route({"type": "state", "body": latest_state["body"], "activity": latest_state["activity"], "updated_at": timestamp}, exclude=ws)
            elif kind in ("reward", "punish"):
                await route({
                    "type": kind,
                    "intensity": max(0.0, min(100.0, float(message.get("intensity", 1.0)))),
                    "duration_ms": max(1, min(60000, int(message.get("duration_ms", 100)))),
                    "timestamp": time.time(),
                })
            elif kind == "sensory_input":
                modality = message.get("modality", "other")
                if modality not in {"vision", "olfaction", "gustation", "mechanosensation", "temperature", "other"}:
                    await ws.send_json({"type": "error", "error": "Unsupported sensory modality"})
                    continue
                await route({
                    "type": "sensory_input", "modality": modality,
                    "values": message.get("values", {}),
                    "duration_ms": max(1, min(60000, int(message.get("duration_ms", 100)))),
                    "timestamp": time.time(),
                })
            elif kind == "neuron:fire":
                neuron_id = message.get("id")
                if not isinstance(neuron_id, int) or neuron_id < 0:
                    await ws.send_json({"type": "error", "error": "id must be a non-negative integer"})
                    continue
                await route({
                    "type": "neuron:fire", "id": neuron_id,
                    "rate": max(0.0, min(1000.0, float(message.get("rate", 100.0)))),
                    "duration_ms": max(1, min(60000, int(message.get("duration_ms", 20)))),
                    "timestamp": time.time(),
                })
            elif kind in ("arena:place", "arena:remove"):
                if kind == "arena:place" and message.get("kind") not in {"sugar", "aversive", "cube"}:
                    await ws.send_json({"type": "error", "error": "kind must be sugar, aversive, or cube"})
                    continue
                try:
                    x, y = float(message.get("x", 0)), float(message.get("y", 0))
                except (TypeError, ValueError):
                    await ws.send_json({"type": "error", "error": "x and y must be numbers"})
                    continue
                await route({
                    "type": kind, "kind": message.get("kind"),
                    "x": x, "y": y, "timestamp": time.time(),
                })
            elif kind == "arena:size":
                try:
                    radius = float(message.get("radius"))
                except (TypeError, ValueError):
                    await ws.send_json({"type": "error", "error": "radius must be a number"})
                    continue
                if not math.isfinite(radius) or not 2 <= radius <= 50:
                    await ws.send_json({"type": "error", "error": "radius must be between 2 and 50"})
                    continue
                await route({"type": "arena:size", "radius": radius, "timestamp": time.time()}, exclude=ws)
            elif kind in ("fly:pose:start", "fly:pose:end"):
                fly_id = message.get("id")
                if not isinstance(fly_id, int) or fly_id < 0:
                    await ws.send_json({"type": "error", "error": "id must be a non-negative integer"})
                    continue
                await route({"type": kind, "id": fly_id, "timestamp": time.time()}, exclude=ws)
            elif kind == "fly:pose":
                fly_id = message.get("id")
                position, quaternion = message.get("position"), message.get("quaternion")
                valid_vector = lambda values, size: isinstance(values, list) and len(values) == size and all(isinstance(v, (int, float)) and math.isfinite(float(v)) for v in values)
                if not isinstance(fly_id, int) or fly_id < 0 or not valid_vector(position, 3) or not valid_vector(quaternion, 4):
                    await ws.send_json({"type": "error", "error": "fly:pose requires a non-negative id, position[3], and quaternion[4]"})
                    continue
                position = [float(v) for v in position]
                quaternion = [float(v) for v in quaternion]
                norm = math.sqrt(sum(v * v for v in quaternion))
                if norm < 1e-8:
                    await ws.send_json({"type": "error", "error": "quaternion must not be zero"})
                    continue
                quaternion = [v / norm for v in quaternion]
                await route({"type": "fly:pose", "id": fly_id, "position": position, "quaternion": quaternion, "timestamp": time.time()}, exclude=ws)
            elif kind == "vision:frame":
                now = time.monotonic()
                if now - vision_last_frame < 0.075:
                    continue
                width, height, pixels = message.get("width"), message.get("height"), message.get("pixels")
                if not isinstance(width, int) or not isinstance(height, int) or not (1 <= width <= 320 and 1 <= height <= 180) or not isinstance(pixels, str) or len(pixels) > 100000:
                    await ws.send_json({"type": "error", "error": "Invalid vision frame dimensions or pixel data"})
                    continue
                try:
                    decoded = base64.b64decode(pixels, validate=True)
                except (binascii.Error, ValueError):
                    await ws.send_json({"type": "error", "error": "Vision pixels must be base64 encoded"})
                    continue
                if len(decoded) != width * height:
                    await ws.send_json({"type": "error", "error": "Vision pixel count does not match frame dimensions"})
                    continue
                image = message.get("image")
                if not isinstance(image, str) or not image.startswith("data:image/jpeg;base64,") or len(image) > 250000:
                    image = None
                vision_last_frame = now
                await route({"type": "vision:frame", "width": width, "height": height, "pixels": pixels, "image": image, "timestamp": time.time()})
            elif kind == "vision:eyes":
                eyes = message.get("eyes")
                if isinstance(eyes, list) and len(eyes) == 2 and all(isinstance(item, str) and item.startswith("data:image/png;base64,") and len(item) < 100000 for item in eyes):
                    await route({"type": "vision:eyes", "eyes": eyes, "timestamp": time.time()}, exclude=ws)
            elif kind == "vision:stop":
                await route({"type": "vision:stop", "timestamp": time.time()})
            elif kind.startswith("controller:") and session is not None and role == "controller":
                await route(message, exclude=ws)
            elif kind == "simulation:status" and session is not None and role == "owner":
                state = message.get("state")
                if state in {"running", "paused", "stopped"}:
                    session["simulation_status"] = state
                    await route({"type": "simulation:status", "state": state, "timestamp": time.time()}, exclude=ws)
            elif kind == "neurons:read" and session is not None and role == "controller":
                ids = message.get("ids")
                names = message.get("names")
                if ids is not None and (not isinstance(ids, list) or any(not isinstance(v, int) for v in ids)):
                    await ws.send_json({"type": "error", "error": "ids must be an array of integer neuron IDs", "requestId": message.get("requestId")})
                    continue
                if names is not None and (not isinstance(names, list) or any(not isinstance(v, str) for v in names)):
                    await ws.send_json({"type": "error", "error": "names must be an array of neuron names", "requestId": message.get("requestId")})
                    continue
                selected = list(neurons.values())
                if ids is not None or names is not None:
                    wanted_ids = set(ids or [])
                    wanted_names = {v.casefold() for v in (names or [])}
                    selected = [n for n in selected if n.get("id") in wanted_ids or str(n.get("name", n.get("type", ""))).casefold() in wanted_names]
                found_ids = {n.get("id") for n in selected}
                found_names = {str(n.get("name", n.get("type", ""))).casefold() for n in selected}
                missing = [v for v in (ids or []) if v not in found_ids] + [v for v in (names or []) if v.casefold() not in found_names]
                try:
                    offset = max(0, int(message.get("offset", 0)))
                    limit = max(1, min(500, int(message.get("limit", 500))))
                except (TypeError, ValueError):
                    await ws.send_json({"type": "error", "error": "offset and limit must be integers", "requestId": message.get("requestId")})
                    continue
                batch = selected[offset:offset + limit]
                next_offset = offset + len(batch) if offset + len(batch) < len(selected) else None
                await ws.send_json({"type": "neurons:read:result", "requestId": message.get("requestId"), "neurons": batch, "missing": missing, "total": len(selected), "offset": offset, "nextOffset": next_offset})
            elif kind in {"simulation:control", "element:command", "property:set", "arena:list", "loom:trigger", "fly:takeoff", "fly:transform:get", "scaffold:set"} and session is not None and role == "controller":
                delivered = await route(message, exclude=ws)
                if delivered == 0:
                    await ws.send_json({"type": "ack", "requestId": message.get("requestId"), "ok": False, "error": "Arena owner is offline"})
            elif kind in {"circuit:list", "circuit:read"} and session is not None and role == "controller":
                if kind == "circuit:read" and not isinstance(message.get("group"), str):
                    await ws.send_json({"type": "error", "error": "group must be a circuit key or label", "requestId": message.get("requestId")})
                    continue
                delivered = await route(message, exclude=ws)
                if delivered == 0:
                    await ws.send_json({"type": "error", "error": "Arena owner is offline", "requestId": message.get("requestId")})
            elif kind in {"ack", "error", "arena:list:result", "neurons:read:result", "fly:transform:result", "circuit:list:result", "circuit:read:result"} and session is not None and role == "owner":
                await route(message, exclude=ws)
            elif kind == "ping":
                await ws.send_json({"type": "pong", "timestamp": time.time()})
            else:
                await ws.send_json({"type": "error", "error": f"Unknown message type: {kind}"})
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(ws)
        if session is not None:
            if role == "owner" and session.get("owner") is ws:
                session["owner"] = None
                for controller in tuple(session["controllers"]):
                    try:
                        await controller.send_json({"type": "session:offline"})
                    except Exception:
                        session["controllers"].discard(controller)
            elif role == "controller":
                session["controllers"].discard(ws)


@app.websocket("/ws/{session_id}")
async def session_websocket_endpoint(ws: WebSocket, session_id: str, role: str = Query(default="controller")):
    await handle_websocket(ws, session_id, role)
