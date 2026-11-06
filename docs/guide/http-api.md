# Simulation HTTP API

The arena starts one HTTP API session per open arena page. The session ID is available in the browser console as `window.__simApi.id`; its base path is `window.__simApi.basePath`. The API is served by the Vite development and preview servers, so run `npm run dev` or `npm run build && npm run preview`. A static GitHub Pages deployment cannot host these stateful endpoints by itself.

All endpoints use JSON. Fly endpoints require the `X-Sim-Target` header, for example `X-Sim-Target: fly 0`. IDs are local to the current arena page.

## Endpoints

| Method | Path | Description |
|---|---|---|
| POST | `/sim` | Create a session (used internally by the arena page) |
| POST | `/sim/{id}/play` | Start all flies |
| POST | `/sim/{id}/pause` | Pause all flies |
| POST | `/sim/{id}/stop` | Stop/pause all flies |
| GET | `/sim/{id}/state` | Get arena and fly state |
| GET / POST | `/sim/{id}/fly/transform` | Read or set the selected fly transform |
| GET | `/sim/{id}/fly/brain` | Get brain summary |
| GET / POST | `/sim/{id}/fly/brain/controls` | Read or update brain controls |

## Transform

`GET /sim/{id}/fly/transform` returns position in arena coordinates and Euler rotation in degrees.

`POST /sim/{id}/fly/transform` accepts either field or both:

```json
{
  "position": [0.2, 0.1, 0.132],
  "rotation": [0, 0, 90]
}
```

The brain endpoint accepts `?offset=0&limit=1000` (maximum page size 1,000). Each record contains the connectome index, cell type, side and accumulated spike count.\n\n## Brain controls

Values for temperature, reward and smell range from -100 to 100. Hunger and courtship range from 0 to 100. Each setting has an `enabled` flag: when false, the corresponding input comes from the simulated environment/body; when true, the slider supplies the input. Resetting a slider sets its value to zero without disabling the override.

```json
{
  "temperature": { "enabled": true, "value": -50 },
  "reward": { "enabled": true, "value": 75 },
  "smell": { "enabled": false, "value": 0 },
  "hunger": { "enabled": true, "value": 80 },
  "courtship": { "enabled": false, "value": 0 }
}
```

POST accepts partial updates, so one setting can be changed without resending the others.

## Example

```sh
curl -X GET "http://localhost:5173/sim/SESSION_ID/fly/transform" \
  -H "X-Sim-Target: fly 0"

curl -X POST "http://localhost:5173/sim/SESSION_ID/fly/brain/controls" \
  -H "Content-Type: application/json" \
  -H "X-Sim-Target: fly 0" \
  -d '{"hunger":{"enabled":true,"value":80}}'
```

The API uses a short-lived request queue between the HTTP server and the browser's fly workers. Requests time out if the browser tab is closed or stops responding.
