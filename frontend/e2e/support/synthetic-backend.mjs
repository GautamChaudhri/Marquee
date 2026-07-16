// A tiny synthetic canonical backend for hermetic E2E runs. It returns
// well-shaped empty job/activity/system payloads so the SvelteKit app can render
// its shell without the live API, database, or media. It never talks to any
// external service. The SvelteKit API proxy targets this server via
// MARQUEE_API_URL (see playwright.config.ts).
import { createServer } from 'node:http';

const PORT = Number(process.env.SYNTHETIC_BACKEND_PORT ?? 3199);

function json(res, status, body) {
	res.writeHead(status, { 'content-type': 'application/json' });
	res.end(JSON.stringify(body));
}

const server = createServer((req, res) => {
	if (req.method !== 'GET') {
		json(res, 200, {});
		return;
	}
	const url = new URL(req.url ?? '/', `http://127.0.0.1:${PORT}`);
	const path = url.pathname;

	if (path === '/api/jobs') {
		const view = url.searchParams.get('view') ?? 'queue';
		json(res, 200, { items: [], limit: 200, next_cursor: null, view });
		return;
	}
	if (path === '/api/system/metrics/history') {
		const now = new Date().toISOString();
		json(res, 200, { window: '1h', start_at: now, end_at: now, points: [], jobs: [] });
		return;
	}
	// Everything else the shell may probe: a benign empty document.
	json(res, 200, {});
});

server.listen(PORT, '127.0.0.1', () => {
	console.log(`synthetic backend listening on http://127.0.0.1:${PORT}`);
});
