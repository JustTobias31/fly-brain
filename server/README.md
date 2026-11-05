# Fly Brain

A local fly-brain simulation with a browser controller and a separate FastAPI HTTP/WebSocket service.

## Requirements

- Python 3.10+ (use a version supported by the dependencies)
- Node.js and npm
- A modern browser with WebSocket support; screen/window/tab capture requires a secure context or localhost

## Start the project

Open two terminals in the repository root.

### 1. Install and start the frontend

```bash
npm install
npm run dev
```

Open the Vite URL printed in the terminal, normally **http://localhost:5173**.

### 2. Install and start the API

In the second terminal:

```bash
python -m pip install -r server/requirements.txt
python -m uvicorn server.app:app --host 127.0.0.1 --port 8000 --reload
```

The API listens at **http://127.0.0.1:8000** and its WebSocket at **ws://127.0.0.1:8000/ws**. Keep both processes running. The controller's HTTP/WebSocket Debug popup shows connection status and logs.

Optional: if the frontend uses a different origin, set `FLY_BRAIN_CORS_ORIGINS` to a comma-separated list of allowed origins before starting Uvicorn. The default allows Vite on localhost/127.0.0.1 ports 5173 and 4173.

## HTTP API

Base URL: `http://127.0.0.1:8000`

All POST requests use JSON. Successful signal endpoints return `{"accepted":true,"delivered_to":N}`; this means the message was broadcast to N connected WebSocket clients, not necessarily that a biological response occurred.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Service status, connected WebSocket client count, registered neuron count, last update time |
| `POST` | `/reward` | Broadcast a reward signal |
| `POST` | `/punish` | Broadcast an aversive/punishment signal |
| `POST` | `/input` | Broadcast generic sensory-only input |
| `GET` | `/neurons` | List registered neuron metadata/activity; supports pagination |
| `GET` | `/neurons` with `X-Neuron-ID` | Return one registered neuron |
| `GET` | `/state` | Latest body and activity telemetry |

### `GET /health`

Example response:

```json
{"status":"ok","websocket_clients":1,"registered_neurons":1200,"updated_at":1730000000.0}
```

### `POST /reward` and `POST /punish`

```json
{"intensity":1.0,"duration_ms":100,"payload":{}}
```

- `intensity`: 0–100, default 1
- `duration_ms`: 1–60000, default 100
- `payload`: optional JSON object

Example:

```bash
curl -X POST http://127.0.0.1:8000/reward -H "Content-Type: application/json" -d '{"intensity":1,"duration_ms":100}'
```

### `POST /input`

Sensory-only input; it is not a direct neuron stimulation endpoint and does not replace optic-ray vision on its own.

```json
{"modality":"olfaction","values":{"odor":"sugar","strength":0.8},"duration_ms":100}
```

Allowed modalities: `vision`, `olfaction`, `gustation`, `mechanosensation`, `temperature`, `other`. Duration is 1–60000 ms.

### `GET /neurons`

- `offset`: default 0, minimum 0
- `limit`: default 1000, range 1–10000

Example: `GET /neurons?offset=0&limit=500`

To retrieve one neuron, send header `X-Neuron-ID: 123`. Invalid IDs return HTTP 400; unregistered IDs return HTTP 404.

### `GET /state`

Returns the latest state object, including `updated_at`, `body`, and `activity`. Values are null until the simulation sends telemetry.

## WebSocket API

Connect to **`ws://127.0.0.1:8000/ws`**. Messages are JSON objects with a `type` field. On connection, the server sends:

```json
{"type":"connected","registered_neurons":0}
```

Use a WebSocket client to send the following messages.

### Simulation → server: registration and telemetry

Register neuron metadata (send in batches if the list is large):

```json
{"type":"neurons:register","neurons":[{"id":123,"name":"example neuron","type":"visual"}]}
```

Update neuron activity:

```json
{"type":"neurons:activity","neurons":[{"id":123,"activity":0.4,"firing_rate":12.0,"spike_count":3}]}
```

Publish body/state telemetry:

```json
{"type":"state","body":{"position":[0,0,0],"orientation":[0,0,0,1]},"activity":{"active_count":1}}
```

### Client → simulation: signals and sensory input

These messages are broadcast to other connected clients:

```json
{"type":"reward","intensity":1.0,"duration_ms":100}
{"type":"punish","intensity":1.0,"duration_ms":100}
{"type":"sensory_input","modality":"olfaction","values":{"strength":0.8},"duration_ms":100}
```

The simulation-side adapter must translate these messages into the FlyAgent sensory/neuromodulation interfaces. The API confirms delivery to clients, not biological effect.

### Direct neuron command

```json
{"type":"neuron:fire","id":123,"rate":100,"duration_ms":20}
```

- `id`: non-negative integer
- `rate`: 0–1000, default 100
- `duration_ms`: 1–60000, default 20

### Arena elements and size

Place an element:

```json
{"type":"arena:place","kind":"sugar","x":0.5,"y":-0.2}
```

Allowed `kind`: `sugar`, `aversive`, `cube`.

Remove an element at coordinates:

```json
{"type":"arena:remove","x":0.5,"y":-0.2}
```

Set square baseplate half-width (radius 2–50):

```json
{"type":"arena:size","radius":20}
```

### Fly pose / gizmo synchronization

Notify the simulation that a remote drag is beginning/ending:

```json
{"type":"fly:pose:start","id":0}
{"type":"fly:pose:end","id":0}
```

Set a fly transform:

```json
{"type":"fly:pose","id":0,"position":[0,0,0.132],"quaternion":[0,0,0,1]}
```

Position is world-space `[x,y,z]`; quaternion is `[x,y,z,w]`. The server normalizes non-zero quaternions. The fly ID must be a non-negative integer.

### External vision frames

Send grayscale pixels as base64, with exactly `width * height` bytes. Maximum dimensions are 320×180; frames are rate-limited to about 13 per second.

```json
{"type":"vision:frame","width":160,"height":90,"pixels":"<base64 grayscale bytes>","image":"data:image/jpeg;base64,<optional jpeg>"}
```

The optional JPEG data URI is used for preview; the grayscale byte array is the machine-readable frame. Stop external vision:

```json
{"type":"vision:stop"}
```

### Ping / errors

```json
{"type":"ping"}
```

Server response:

```json
{"type":"pong","timestamp":1730000000.0}
```

Invalid or unknown message types produce:

```json
{"type":"error","error":"description"}
```

Neuron registration receives `neurons:registered`; malformed messages may receive `error`. State/activity updates are stored by the server and available through `GET /state`.

## Security note

This API is intended for local development. Do not expose it directly to the public internet without authentication, authorization, and stricter origin/network controls.
