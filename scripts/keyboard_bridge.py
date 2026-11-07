#!/usr/bin/env python3
"""Follow the selected fly's position and heading with Roblox arrow keys.

Install: py -m pip install websockets pynput
Run:     py scripts/keyboard_bridge.py SESSION_ID
Optional: --fly-id 2 --studs-per-unit 3.57

Roblox assumptions: WalkSpeed 16; a quick left/right tap turns the camera 5 degrees.
Focus the Roblox window before running. Ctrl+C releases every held key.
"""
import argparse
import asyncio
import json
import math
import sys
from itertools import count

import websockets
from pynput.keyboard import Controller, Key

ARROWS = {"forward": Key.up, "backward": Key.down, "left": Key.left, "right": Key.right}
GIANT_FIBRE_KEY = Key.space
TURN_STEP_DEG = 5.0

class PositionBridge:
    def __init__(self, fly_id, interval, studs_per_unit, turn_sign, max_step):
        self.fly_id = fly_id
        self.interval = interval
        self.studs_per_unit = studs_per_unit
        self.turn_sign = turn_sign
        self.max_step = max_step
        self.keyboard = Controller()
        self.ids = count(1)
        self.previous = None
        self.turn_remainder = 0.0
        self.giant_fibre_active = False

    async def request_transform(self, ws):
        request_id = f"position-bridge-{next(self.ids)}"
        message = {"type": "fly:transform:get", "requestId": request_id}
        if self.fly_id is not None:
            message["flyId"] = self.fly_id
        await ws.send(json.dumps(message))
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=3)
            response = json.loads(raw)
            if response.get("requestId") != request_id:
                if response.get("type") == "error":
                    raise RuntimeError(response.get("error", "WebSocket API error"))
                continue
            if response.get("type") == "error" or response.get("error"):
                raise RuntimeError(response.get("error", "Could not read fly transform"))
            if not all(k in response for k in ("position", "rotation")):
                raise RuntimeError(f"Unexpected transform response: {response}")
            return response

    async def check_giant_fibre(self, ws):
        request_id = f"giant-fibre-{next(self.ids)}"
        await ws.send(json.dumps({
            "type": "neurons:read",
            "requestId": request_id,
            "names": ["DNp01"],
            "limit": 10,
        }))
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=3)
            response = json.loads(raw)
            if response.get("requestId") != request_id:
                if response.get("type") == "error":
                    raise RuntimeError(response.get("error", "WebSocket API error"))
                continue
            if response.get("type") == "error":
                raise RuntimeError(response.get("error", "Could not read Giant Fibre activity"))
            neurons = response.get("neurons", [])
            active = []
            for neuron in neurons:
                try:
                    activity = float(neuron.get("activity", 0.0))
                except (TypeError, ValueError):
                    activity = 0.0
                if activity > 0:
                    active.append((neuron.get("id"), activity))

            if active and not self.giant_fibre_active:
                details = ", ".join(f"{neuron_id}: {activity:.4f}" for neuron_id, activity in active)
                print(f"Giant Fibre active ({details}) -> SPACE", flush=True)
                self.keyboard.press(GIANT_FIBRE_KEY)
                try:
                    await asyncio.sleep(0.06)
                finally:
                    self.keyboard.release(GIANT_FIBRE_KEY)
            self.giant_fibre_active = bool(active)
            return

    @staticmethod
    def yaw_from_quaternion(q):
        x, y, z, w = map(float, q)
        return math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))

    @staticmethod
    def angle_delta(current, previous):
        return (current - previous + math.pi) % (2 * math.pi) - math.pi

    async def tap_turn(self, direction):
        key = ARROWS[direction]
        self.keyboard.press(key)
        await asyncio.sleep(0.045)
        self.keyboard.release(key)
        await asyncio.sleep(0.035)

    async def move(self, direction, distance):
        duration = distance * self.studs_per_unit / 16.0
        if duration <= 0.005:
            return
        key = ARROWS[direction]
        self.keyboard.press(key)
        try:
            await asyncio.sleep(duration)
        finally:
            self.keyboard.release(key)

    async def update(self, transform):
        position = tuple(map(float, transform["position"]))
        yaw = self.yaw_from_quaternion(transform["rotation"])
        fly_id = transform.get("flyId", self.fly_id)
        if self.previous is None:
            self.previous = (position, yaw)
            print(f"Tracking fly {fly_id}; initial position recorded. No movement yet.")
            return

        old_position, old_yaw = self.previous
        self.previous = (position, yaw)
        dx, dy = position[0] - old_position[0], position[1] - old_position[1]
        distance = math.hypot(dx, dy)
        turn = self.angle_delta(yaw, old_yaw) * self.turn_sign
        self.turn_remainder += math.degrees(turn)
        taps = int(abs(self.turn_remainder) // TURN_STEP_DEG)
        direction = "right" if self.turn_remainder > 0 else "left"
        taps = min(taps, 12)
        for _ in range(taps):
            await self.tap_turn(direction)
        if taps:
            self.turn_remainder -= math.copysign(taps * TURN_STEP_DEG, self.turn_remainder)

        if distance > self.max_step:
            print(f"Ignored position jump ({distance:.3f} sim units); recalibrated baseline.")
            return
        if distance > 1e-5:
            # Project displacement onto the fly's current forward direction.
            forward_x, forward_y = math.cos(yaw), math.sin(yaw)
            forward_distance = dx * forward_x + dy * forward_y
            if abs(forward_distance) > 1e-5:
                await self.move("forward" if forward_distance > 0 else "backward", abs(forward_distance))

        print(f"Fly {fly_id} | Δx {dx:+.3f} Δy {dy:+.3f} | turn {math.degrees(turn):+.1f}° | taps {taps} | move {distance:.3f}", flush=True)

    async def run(self, url):
        print("Connecting:", url)
        async with websockets.connect(url, ping_interval=15, ping_timeout=None, open_timeout=10, close_timeout=5, max_queue=32) as ws:
            hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            if hello.get("type") != "connected":
                raise RuntimeError("Unexpected WebSocket handshake: " + str(hello))
            print("Connected. Focus Roblox now; Ctrl+C stops the bridge.")
            while True:
                transform = await self.request_transform(ws)
                await self.update(transform)
                await self.check_giant_fibre(ws)
                await asyncio.sleep(self.interval)

def main():
    parser = argparse.ArgumentParser(description="Mirror fly position and heading with Roblox arrow keys")
    parser.add_argument("session_id", help="Session ID from ws://localhost:5173/ws/SESSION_ID")
    parser.add_argument("--url", help="Full WebSocket URL")
    parser.add_argument("--fly-id", type=int, help="Fly ID; defaults to the currently selected fly")
    parser.add_argument("--interval", type=float, default=0.2, help="Position polling interval in seconds")
    parser.add_argument("--studs-per-unit", type=float, default=160.0, help="Roblox studs per simulator position unit (0.1 world units = 16 studs)")
    parser.add_argument("--turn-sign", type=float, choices=(-1.0, 1.0), default=1.0, help="Invert left/right camera mapping with -1")
    parser.add_argument("--max-step", type=float, default=0.5, help="Ignore larger position jumps as teleports")
    args = parser.parse_args()
    if args.interval < 0.03 or args.studs_per_unit <= 0 or args.max_step <= 0:
        parser.error("interval, studs-per-unit and max-step must be positive (interval >= 0.03)")
    url = args.url or f"ws://localhost:5173/ws/{args.session_id}"
    bridge = PositionBridge(args.fly_id, args.interval, args.studs_per_unit, args.turn_sign, args.max_step)
    try:
        asyncio.run(bridge.run(url))
    except KeyboardInterrupt:
        print("\nStopped.")
    except Exception as exc:
        print("\nBridge error:", exc, file=sys.stderr)
        return 1
    finally:
        for key in (*ARROWS.values(), GIANT_FIBRE_KEY):
            bridge.keyboard.release(key)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
