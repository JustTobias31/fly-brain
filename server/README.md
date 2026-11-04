# Local simulation API

Install and run from the repository root:

```bash
python -m pip install -r server/requirements.txt
python -m uvicorn server.app:app --host 127.0.0.1 --port 8000 --reload
```

The API is deliberately a separate process from Vite. The browser connects to
`ws://127.0.0.1:8000/ws`; HTTP endpoints are served at `http://127.0.0.1:8000`.

## Endpoints

- `POST /reward` — reward signal: `{"intensity":1,"duration_ms":100}`
- `POST /punish` — aversive signal, same shape
- `POST /input` — generic sensory-only input. It does not replace the visual ray input by itself.
- `WS /ws` `vision:frame` — external grayscale frame stream; frames are relayed to the controller and sampled by the fly’s optic-lobe input. The controller’s External vision switch opens the browser screen/window/tab picker and sends these frames automatically.
- `GET /neurons` — registered neuron metadata and current activity
- `GET /neurons` with `X-Neuron-ID: 123` — one neuron
- `GET /state` — latest body/activity telemetry
- `GET /health` — API status
- `WS /ws` — simulation telemetry and control messages

WebSocket messages from the simulation client:
- `neurons:register`: `{"type":"neurons:register","neurons":[{"id":123,"name":"..."}]}`
- `neurons:activity`: `{"type":"neurons:activity","neurons":[{"id":123,"activity":0.4}]}`
- `state`: `{"type":"state","body":{"position":[0,0,0],"orientation":[0,0,0,1]}}`
- `neuron:fire`: `{"type":"neuron:fire","id":123,"rate":100,"duration_ms":20}`
- `arena:place`: `{"type":"arena:place","kind":"sugar","x":0.5,"y":-0.2}` (kind: `sugar`, `aversive`, or `cube`)
- `arena:remove`: `{"type":"arena:remove","x":0.5,"y":-0.2}`
- `reward`, `punish`, and `sensory_input` can also be sent over the WebSocket; they are broadcast to connected controller clients.
- `vision:frame`: `{"type":"vision:frame","width":160,"height":90,"pixels":"<base64 grayscale bytes>","image":"data:image/jpeg;base64,..."}`. Pixel data must contain exactly `width * height` bytes; dimensions are limited to 320×180 and frames are rate-limited. `vision:stop` restores arena raycast vision.

Neuron listing is paginated: `GET /neurons?offset=0&limit=1000`. A single neuron can be fetched with the `X-Neuron-ID` header.

HTTP signal requests are broadcast to connected WebSocket clients. The browser
simulation still needs its client-side adapter to translate those messages into
the existing FlyAgent sensory and neuromodulation interfaces; the API does not
pretend that a signal has affected the brain until that adapter acknowledges it.

This server is intended for local development, not exposure directly to the
public internet. Add authentication and stricter origin controls before remote use.
