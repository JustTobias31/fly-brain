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


## Controller → simulation commands

These commands are implemented for session WebSockets: a controller sends them to the session, the server routes them to the connected arena owner, and the owner returns acknowledgements/results through the same session. The arena must be loaded and connected as the session owner. Unsupported properties, missing targets, and invalid values return an error acknowledgement.

All commands are JSON objects with a `type` string. Include a unique `requestId` when you want to match a reply to a request. The examples below show the proposed message shape.

### 1. Simulation playback

Use one message for all three states:

```json
{"type":"simulation:control","action":"play","requestId":"sim-1"}
{"type":"simulation:control","action":"pause","requestId":"sim-2"}
{"type":"simulation:control","action":"stop","requestId":"sim-3"}
```

- `action`: `play`, `pause`, or `stop`.
- `play` resumes/starts the simulation, `pause` freezes it while retaining its state, and `stop` stops the current run.

### 2. Place, move, rotate, and remove elements

Use `element:command` for arena objects and flies:

```json
{"type":"element:command","action":"place","element":"cube","position":[1,0,0.5],"properties":{"size":[1,1,1]},"requestId":"el-1"}
{"type":"element:command","action":"move","id":"block-12","position":[2,0,0.5]}
{"type":"element:command","action":"rotate","id":"block-12","rotation":[0,0,0,1]}
{"type":"element:command","action":"remove","id":"block-12"}
```

- `action`: `place`, `move`, `rotate`, or `remove`.
- `element`: element type/name to create, such as `cube`, `sugar`, `odor`, `fly`, or `fly female`. Required for `place`.
- `id`: ID returned by `place` or `arena:list` for `move`, `rotate`, and `remove`. Fly IDs are numeric.
- `position`: world-space `[x,y,z]`.
- `rotation`: quaternion `[x,y,z,w]`.
- `properties`: optional initial properties for a newly placed element.
- `name`: optional display/name value for the created element.

**Fly creation shortcut:** when `action` is `place` and `element` is `fly` or `fly female` (case-insensitive), create a fly instead of an arena prop. The optional `name` can identify the new fly. On success, include the assigned display name in the success/acknowledgement packet as a top-level `name` field, formatted as `fly <idx>` (for example, `"fly 0"`). Also include the new fly's ID in `result.id`.

Example successful response:

```json
{"type":"ack","requestId":"el-1","ok":true,"name":"fly 0","result":{"id":0}}
```

The `idx` is the index assigned by the simulation to that newly created fly.

### 3. Change an element or fly property

```json
{"type":"property:set","target":{"type":"element","id":"block-12"},"property":"size","value":[2,1,1],"requestId":"prop-1"}
{"type":"property:set","target":{"type":"fly","id":0},"property":"movementEnabled","value":false}
```

- `target.type`: `element` or `fly`.
- `target.id`: target ID.
- `property`: property key supported by that target.
- `value`: JSON value (number, string, boolean, array, or object).
- Reject unknown or read-only properties with an `error` reply rather than silently accepting them.

### 4. Get the arena block/element list

```json
{"type":"arena:list","requestId":"list-1"}
```

Expected reply:

```json
{
  "type":"arena:list:result",
  "requestId":"list-1",
  "elements":[
    {
      "id":"block-12",
      "type":"cube",
      "name":"Test block",
      "position":[2,0,0.5],
      "rotation":[0,0,0,1],
      "properties":{"size":[2,1,1]}
    }
  ]
}
```

Return every current arena element, including its stable ID, type, position, rotation, and editable properties. Use an empty array when the arena contains no elements.

### 5. Trigger the loom threat (jumpscare)

```json
{"type":"loom:trigger","requestId":"loom-1"}
```

This starts the existing loom-threat sequence. It is a trigger command, not a request to create a new arena object.

### 11. Get a fly's position and rotation

Request the current transform of one fly:

```json
{"type":"fly:transform:get","flyId":0,"requestId":"transform-1"}
```

Expected reply:

```json
{
  "type":"fly:transform:result",
  "requestId":"transform-1",
  "flyId":0,
  "position":[0,0,0.132],
  "rotation":[0,0,0,1]
}
```

- `flyId`: ID/index of the fly to query.
- `position`: current world-space position as `[x,y,z]`.
- `rotation`: current orientation as quaternion `[x,y,z,w]`, matching the format used by `fly:pose`.
- Return the latest simulation transform. If the fly does not exist, return an error instead of a fabricated/default transform.

### 6. Make a fly take off

```json
{"type":"fly:takeoff","flyId":0,"requestId":"takeoff-1"}
```

- `flyId`: optional; when omitted, apply the takeoff command to the currently selected/default fly.
- The simulation should acknowledge whether takeoff was accepted.

### 7. Read neuron activity

Request selected neurons by numeric IDs and/or names:

```json
{"type":"neurons:read","ids":[123,456],"requestId":"read-1"}
{"type":"neurons:read","names":["example neuron","another neuron"]}
```

Omit both `ids` and `names` to request the available neuron readout. Expected reply:

```json
{
  "type":"neurons:read:result",
  "requestId":"read-1",
  "neurons":[
    {"id":123,"name":"example neuron","activity":0.4,"firing_rate":12.0,"spike_count":3}
  ]
}
```

Return the latest available values for each matched neuron. Unknown IDs/names are reported in `missing`. Results are paged in batches of up to 500; use `offset` and `limit` to retrieve later batches. The reply includes `total`, `offset`, and `nextOffset`.

### 8. Set the scaffold

```json
{"type":"scaffold:set","scaffold":"walking","requestId":"scaffold-1"}
```

`scaffold` may be a registered scaffold name or a JSON object describing a scaffold configuration. The simulation should validate it, apply it, and return an error if the requested scaffold is unsupported.

### 9–10. Visual stream

Send frames using the existing `vision:frame` message shape:

```json
{"type":"vision:frame","width":160,"height":90,"pixels":"<base64 grayscale bytes>","image":"data:image/jpeg;base64,<optional jpeg>"}
```

The **first valid frame automatically enables the visual stream**; clients do not need a separate start command. Continue sending frames while streaming. To disable the stream:

```json
{"type":"vision:stop","requestId":"vision-stop-1"}
```

The frame dimensions, pixel encoding, preview image, and rate limits are the same as the [External vision frames](#external-vision-frames) section above.

### Replies and errors

For command acknowledgements, use a consistent reply shape:

```json
{"type":"ack","requestId":"el-1","ok":true,"result":{"id":"block-12"}}
{"type":"ack","requestId":"el-2","ok":false,"error":"Element not found"}
```

- Echo the incoming `requestId` in the reply when one was supplied.
- Include returned data in `result` for commands that create or modify objects.
- Use the dedicated result message types shown above for list and neuron-read requests.
- Send `{"type":"error","error":"description","requestId":"..."}` for malformed or unsupported commands.
- Keep the WebSocket responsive: large lists/readouts should be returned in bounded batches if necessary.

## Security note

This API is intended for local development. Do not expose it directly to the public internet without authentication, authorization, and stricter origin/network controls.
