#!/usr/bin/env python3
"""Map fly motor-circuit activity to arrow keys.
Install: py -m pip install websockets pynput
Run:     py scripts/keyboard_bridge.py SESSION_ID
Stop with Ctrl+C; held keys are released on exit.
"""
import argparse
import asyncio
import json
import sys
from itertools import count
import websockets
from pynput.keyboard import Controller, Key

KEYS = {"left": Key.left, "right": Key.right, "forward": Key.up, "backward": Key.down}

class Bridge:
    def __init__(self, threshold, interval):
        self.keyboard = Controller()
        self.threshold = threshold
        self.interval = interval
        self.held = set()
        self.ids = count(1)

    def set_keys(self, wanted):
        for name in self.held - wanted:
            self.keyboard.release(KEYS[name])
        for name in wanted - self.held:
            self.keyboard.press(KEYS[name])
        self.held = set(wanted)

    def release_all(self):
        self.set_keys(set())

    async def request(self, ws, kind, group=None):
        request_id = "keybridge-" + str(next(self.ids))
        msg = {"type": kind, "requestId": request_id}
        if group is not None:
            msg["group"] = group
        await ws.send(json.dumps(msg))
        while True:
            response = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
            if response.get("requestId") != request_id:
                if response.get("type") == "error":
                    raise RuntimeError(response.get("error", "API error"))
                continue
            if response.get("type") == "error" or response.get("error"):
                raise RuntimeError(response.get("error", "Circuit read failed"))
            return response

    def update(self, steering, forward, backward):
        left = max(0.0, float(steering.get("leftHz") or 0))
        right = max(0.0, float(steering.get("rightHz") or 0))
        fwd = (float(forward.get("leftHz") or 0) + float(forward.get("rightHz") or 0)) / 2
        back = (float(backward.get("leftHz") or 0) + float(backward.get("rightHz") or 0)) / 2
        wanted = set()
        if left >= self.threshold and left > right:
            wanted.add("left")
        elif right >= self.threshold and right > left:
            wanted.add("right")
        if fwd >= self.threshold and fwd >= back:
            wanted.add("forward")
        elif back >= self.threshold:
            wanted.add("backward")
        self.set_keys(wanted)
        print(f"Steer L/R {left:.2f}/{right:.2f} Hz | forward {fwd:.2f} | backward {back:.2f} | keys: {', '.join(sorted(wanted)) or '-'}", end="\r", flush=True)

    async def run(self, url):
        print("Connecting:", url)
        async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
            hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            if hello.get("type") != "connected":
                raise RuntimeError("Unexpected WebSocket handshake: " + str(hello))
            print("Connected to session", hello.get("session_id"))
            listing = await self.request(ws, "circuit:list")
            keys = {g.get("key") for g in listing.get("groups", [])}
            missing = {"steer", "walk", "back"} - keys
            if missing:
                raise RuntimeError("Missing circuit groups: " + ", ".join(sorted(missing)))
            print("Reading motor activity. Focus the application you want to control; Ctrl+C stops.")
            while True:
                try:
                    steer = await self.request(ws, "circuit:read", "steer")
                    walk = await self.request(ws, "circuit:read", "walk")
                    back = await self.request(ws, "circuit:read", "back")
                    self.update(steer.get("group", {}), walk.get("group", {}), back.get("group", {}))
                    await asyncio.sleep(self.interval)
                except (asyncio.TimeoutError, websockets.ConnectionClosed):
                    self.release_all()
                    raise

def main():
    parser = argparse.ArgumentParser(description="Control arrow keys from fly motor-circuit activity")
    parser.add_argument("session_id", help="ID from ws://localhost:5173/ws/SESSION_ID")
    parser.add_argument("--url", help="Full WebSocket URL")
    parser.add_argument("--threshold", type=float, default=0.5, help="Minimum firing rate in Hz")
    parser.add_argument("--interval", type=float, default=0.1, help="Read interval in seconds")
    args = parser.parse_args()
    if args.threshold < 0 or args.interval < 0.02:
        parser.error("threshold must be non-negative and interval at least 0.02 seconds")
    url = args.url or f"ws://localhost:5173/ws/{args.session_id}"
    bridge = Bridge(args.threshold, args.interval)
    try:
        asyncio.run(bridge.run(url))
    except KeyboardInterrupt:
        print("\nStopped.")
    except Exception as exc:
        print("\nBridge error:", exc, file=sys.stderr)
        return 1
    finally:
        bridge.release_all()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
