/** Same-origin → FastAPI proxy. The browser and `load()` call `/api/*`; this
 *  forwards to the backend with the server-only API key attached, so the key
 *  never reaches the client. Response bodies stream through (SSE-safe). */
import { env } from '$env/dynamic/private';
import type { RequestHandler } from './$types';

const BASE = () => env.MARQUEE_API_URL ?? 'http://localhost:3165';

// A hung backend request must not hold a browser connection slot forever — the
// browser caps ~6 per origin, so a few slow proxied calls (e.g. a 4K preview)
// freeze the whole UI until a manual refresh. Time out non-stream requests so a
// slow backend returns 504 fast and frees the slot. SSE is long-lived by design
// and exempted.
const PROXY_TIMEOUT_MS = 20000;

const handler: RequestHandler = async ({ request, params, url, fetch }) => {
	const target = `${BASE()}/api/${params.path}${url.search}`;

	const headers = new Headers(request.headers);
	headers.delete('host');
	headers.delete('connection');
	headers.delete('content-length');
	if (env.MARQUEE_API_KEY) headers.set('X-Api-Key', env.MARQUEE_API_KEY);

	const init: RequestInit = { method: request.method, headers };
	if (request.method !== 'GET' && request.method !== 'HEAD') {
		init.body = await request.arrayBuffer();
	}

	// Event streams (SSE) stay open indefinitely — never time them out.
	const isStream =
		params.path.endsWith('/events') || request.headers.get('accept') === 'text/event-stream';
	if (!isStream) init.signal = AbortSignal.timeout(PROXY_TIMEOUT_MS);

	let res: Response;
	try {
		res = await fetch(target, init);
	} catch (err) {
		if (err instanceof DOMException && err.name === 'TimeoutError') {
			return new Response('Upstream timed out', { status: 504 });
		}
		throw err;
	}

	const respHeaders = new Headers(res.headers);
	respHeaders.delete('content-encoding');
	respHeaders.delete('content-length');
	return new Response(res.body, { status: res.status, headers: respHeaders });
};

export const GET = handler;
export const POST = handler;
export const PUT = handler;
export const PATCH = handler;
export const DELETE = handler;
