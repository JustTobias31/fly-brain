#!/usr/bin/env python3
"""Mirror the selected fly's movement with relative mouse movement.

Install: py -m pip install websockets
Run:     py scripts/keyboard_bridge.py SESSION_ID
Optional: --fly-id 2 --studs-per-unit 160

Forward/backward/left/right movement becomes relative mouse movement.
Positive DNp01 activity produces one left click per firing event.
Focus the target window before running. Ctrl+C stops the bridge.
"""
import argparse
import asyncio
import ctypes
import json
import math
import sys
from itertools import count

import websockets

if sys.platform != "win32":
    raise SystemExit("This bridge currently requires Windows.")

user32 = ctypes.windll.user32
INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long), ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_void_p)
    ]

class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("mi", MOUSEINPUT)]

def send_mouse(dx=0, dy=0, flags=MOUSEEVENTF_MOVE):
    event = INPUT()
    event.type = INPUT_MOUSE
    event.mi.dx = int(dx)
    event.mi.dy = int(dy)
    event.mi.dwFlags = flags
    if user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT)) != 1:
        raise ctypes.WinError()

def move_mouse(dx, dy):
    if dx or dy:
        send_mouse(dx, dy)

def click_left():
    events = (INPUT * 2)()
    events[0].type = INPUT_MOUSE
    events[0].mi.dwFlags = MOUSEEVENTF_LEFTDOWN
    events[1].type = INPUT_MOUSE
    events[1].mi.dwFlags = MOUSEEVENTF_LEFTUP
    if user32.SendInput(2, events, ctypes.sizeof(INPUT)) != 2:
        raise ctypes.WinError()

class PositionBridge:
    def __init__(self, fly_id, interval, pixels_per_unit, turn_sign, max_step):
        self.fly_id = fly_id
        self.interval = interval
        self.pixels_per_unit = pixels_per_unit
        self.turn_sign = turn_sign
        self.max_step = max_step
        self.dnp01_was_active = False
        self.ids = count(1)
        self.previous = None
        self.giant_was_active = False
        self.mouse_remainder_x = 0.0
        self.mouse_remainder_y = 0.0

    async def start_streams(self, ws):
        self.transform_request_id = "mouse-bridge-transform"
        self.neuron_request_id = "mouse-bridge-dnp01"
        transform = {
            "type": "fly:transform:get",
            "requestId": self.transform_request_id,
            "constant": True,
            "intervalMs": max(50, min(5000, int(self.interval * 1000))),
        }
        if self.fly_id is not None:
            transform["flyId"] = self.fly_id
        neurons = {
            "type": "neurons:read",
            "requestId": self.neuron_request_id,
            "names": ["DNp01"],
            "limit": 10,
            "constant": True,
            "intervalMs": max(50, min(5000, int(self.interval * 1000))),
        }
        await ws.send(json.dumps(transform))
        await ws.send(json.dumps(neurons))
        print(f"Subscribed to fly transform and DNp01 streams ({transform['intervalMs']} ms).", flush=True)

    @staticmethod
    def yaw_from_quaternion(q):
        x, y, z, w = map(float, q)
        return math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))

    def update_dnp01(self, neurons):
        active_neurons = [
            (neuron, float(neuron.get("activity") or 0.0))
            for neuron in neurons
            if float(neuron.get("activity") or 0.0) > 0.0
        ]
        active = bool(active_neurons)
        if active and not self.dnp01_was_active:
            details = ", ".join(
                f"{neuron.get('name', 'DNp01')}#{neuron.get('id', '?')}={activity:g}"
                for neuron, activity in active_neurons
            )
            print(f"DNp01 activity detected: {details} -> left click", flush=True)
            click_left()
        self.dnp01_was_active = active

    async def update(self, transform):
        position = tuple(map(float, transform["position"]))
        yaw = self.yaw_from_quaternion(transform["rotation"])
        fly_id = transform.get("flyId", self.fly_id)
        if self.previous is None:
            self.previous = (position, yaw)
            print(f"Tracking fly {fly_id}; initial position recorded.", flush=True)
            return

        old_position, old_yaw = self.previous
        self.previous = (position, yaw)
        dx, dy = position[0] - old_position[0], position[1] - old_position[1]
        distance = math.hypot(dx, dy)
        if distance > self.max_step:
            print(f"Ignored position jump ({distance:.3f} sim units); recalibrated baseline.", flush=True)
            return

        # Project world displacement onto the fly's forward and right axes.
        forward_x, forward_y = math.cos(yaw), math.sin(yaw)
        right_x, right_y = -forward_y, forward_x
        forward = dx * forward_x + dy * forward_y
        lateral = dx * right_x + dy * right_y

        # Forward = mouse up; backward = down; left = left; right = right.
        self.mouse_remainder_x += lateral * self.pixels_per_unit
        self.mouse_remainder_y -= forward * self.pixels_per_unit
        move_x = math.trunc(self.mouse_remainder_x)
        move_y = math.trunc(self.mouse_remainder_y)
        self.mouse_remainder_x -= move_x
        self.mouse_remainder_y -= move_y
        move_mouse(move_x, move_y)

        turn = ((yaw - old_yaw + math.pi) % (2 * math.pi) - math.pi) * self.turn_sign
        print(f"Fly {fly_id} | Δx {dx:+.3f} Δy {dy:+.3f} | mouse {move_x:+d},{move_y:+d} | turn {math.degrees(turn):+.1f}°", flush=True)

    async def run(self, url):
        print("Connecting:", url)
        async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
            hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            if hello.get("type") != "connected":
                raise RuntimeError("Unexpected WebSocket handshake: " + str(hello))
            print("Connected. Focus the target window; Ctrl+C stops the bridge.")
            await self.start_streams(ws)
            try:
                while True:
                    raw = await ws.recv()
                    try:
                        response = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if not isinstance(response, dict):
                        continue
                    request_id = response.get("requestId")
                    if response.get("type") == "error":
                        if request_id in {self.transform_request_id, self.neuron_request_id}:
                            raise RuntimeError(response.get("error", "WebSocket stream failed"))
                        continue
                    if request_id == self.transform_request_id:
                        if response.get("type") == "fly:transform:result" and all(k in response for k in ("position", "rotation")):
                            await self.update(response)
                    elif request_id == self.neuron_request_id:
                        if response.get("type") == "neurons:read:result":
                            neurons = response.get("neurons", [])
                            matches = [
                                neuron for neuron in neurons
                                if str(neuron.get("name", neuron.get("type", ""))).casefold() == "dnp01"
                            ]
                            if matches:
                                self.update_dnp01(matches)
            finally:
                for request_id in (self.transform_request_id, self.neuron_request_id):
                    stop = {"type": "streamstop", "requestId": request_id}
                    try:
                        await ws.send(json.dumps(stop))
                    except Exception:
                        pass

def main():
    parser = argparse.ArgumentParser(description="Mirror fly movement with the mouse and DNp01-triggered clicks")
    parser.add_argument("session_id", help="Session ID from ws://localhost:5173/ws/SESSION_ID")
    parser.add_argument("--url", help="Full WebSocket URL")
    parser.add_argument("--fly-id", type=int, help="Fly ID; defaults to the currently selected fly")
    parser.add_argument("--interval", type=float, default=0.1, help="Polling interval in seconds")
    parser.add_argument("--studs-per-unit", type=float, default=160.0, help="Mouse pixels per simulator unit")
    parser.add_argument("--turn-sign", type=float, choices=(-1.0, 1.0), default=1.0, help="Invert heading sign with -1")
    parser.add_argument("--max-step", type=float, default=0.5, help="Ignore larger position jumps as teleports")
    args = parser.parse_args()
    if args.interval < 0.03 or args.studs_per_unit <= 0 or args.max_step <= 0:
        parser.error("interval, studs-per-unit and max-step must be positive")
    url = args.url or f"ws://localhost:5173/ws/{args.session_id}"
    bridge = PositionBridge(args.fly_id, args.interval, args.studs_per_unit, args.turn_sign, args.max_step)
    try:
        asyncio.run(bridge.run(url))
    except KeyboardInterrupt:
        print("\nStopped.")
    except Exception as exc:
        print("\nBridge error:", exc, file=sys.stderr)
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
