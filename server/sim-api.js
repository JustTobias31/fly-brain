import { randomUUID } from 'node:crypto';

const sessions = new Map();
const json = (res, status, value) => {
  res.statusCode = status;
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  res.end(JSON.stringify(value));
};
const readBody = req => new Promise((resolve, reject) => {
  let body = '', size = 0;
  req.on('data', chunk => { size += chunk.length; if (size > 1_000_000) { reject(Object.assign(new Error('Request body too large'), { status: 413 })); req.destroy(); return; } body += chunk; });
  req.on('end', () => { try { resolve(body ? JSON.parse(body) : {}); } catch { reject(Object.assign(new Error('Invalid JSON body'), { status: 400 })); } });
  req.on('error', reject);
});
function enqueue(session, res, command) {
  if (!session.connected) return json(res, 503, { error: 'The simulation browser is not connected.' });
  const requestId = randomUUID();
  const timeout = setTimeout(() => {
    session.pending.delete(requestId);
    json(res, 504, { error: 'The simulation did not respond in time.' });
  }, 10000);
  session.pending.set(requestId, { res, timeout });
  session.queue.push({ ...command, requestId });
}
async function handle(req, res) {
  const url = new URL(req.url, 'http://localhost');
  const parts = url.pathname.split('/').filter(Boolean);
  if (req.method === 'POST' && parts.length === 1 && parts[0] === 'sim') {
    const id = randomUUID().replaceAll('-', '').slice(0, 20);
    sessions.set(id, { connected: false, queue: [], pending: new Map(), createdAt: Date.now() });
    return json(res, 201, { id, basePath: `/sim/${id}` });
  }
  if (parts[0] !== 'sim' || parts.length < 2) return json(res, 404, { error: 'Unknown API endpoint.' });
  const id = parts[1], session = sessions.get(id);
  if (!session) return json(res, 404, { error: 'Unknown simulation ID.' });
  const path = parts.slice(2).join('/');
  if (req.method === 'POST' && path === 'connect') { session.connected = true; return json(res, 200, { success: true }); }
  if (req.method === 'GET' && path === 'commands') {
    const command = session.queue.shift();
    if (!command) { res.statusCode = 204; return res.end(); }
    return json(res, 200, command);
  }
  if (req.method === 'POST' && path === 'results') {
    const body = await readBody(req), pending = session.pending.get(body.requestId);
    if (!pending) return json(res, 404, { error: 'Unknown or expired request ID.' });
    clearTimeout(pending.timeout); session.pending.delete(body.requestId);
    if (body.error) json(pending.res, body.status || 500, { error: body.error });
    else json(pending.res, 200, body.result ?? null);
    return json(res, 200, { success: true });
  }
  const target = req.headers['x-sim-target'];
  if (path.startsWith('fly/')) {
    if (!target) return json(res, 400, { error: 'Missing X-Sim-Target header.' });
    if (!/^fly(?:\s+|:)(\d+)$/i.test(String(target).trim())) return json(res, 400, { error: 'X-Sim-Target must identify a fly, e.g. "fly 0".' });
  }
  let action, body = {};
  if (['play', 'pause', 'stop'].includes(path) && req.method === 'POST') action = path;
  else if (path === 'state' && req.method === 'GET') action = 'state';
  else if (path === 'fly/transform' && ['GET', 'POST'].includes(req.method)) { action = 'transform'; if (req.method === 'POST') body = await readBody(req); }
  else if (path === 'fly/brain' && req.method === 'GET') action = 'brain';
  else if (path === 'fly/brain/controls' && ['GET', 'POST'].includes(req.method)) { action = 'controls'; if (req.method === 'POST') body = await readBody(req); }
  else return json(res, 404, { error: 'Unknown endpoint or unsupported method.' });
  return enqueue(session, res, { action, method: req.method, target: target ? String(target).trim() : null, body });
}
function middleware(req, res, next) {
  if (!req.url?.startsWith('/sim')) return next();
  handle(req, res).catch(error => {
    if (!res.headersSent) json(res, error.status || 500, { error: error.message || 'Internal API error.' });
  });
}
export function simApi() {
  return {
    name: 'fly-simulation-http-api',
    configureServer(server) { server.middlewares.use(middleware); },
    configurePreviewServer(server) { server.middlewares.use(middleware); },
  };
}
