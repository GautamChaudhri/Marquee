/** Same-origin → FastAPI proxy. The browser and `load()` call `/api/*`; this
 *  forwards to the backend with the server-only API key attached, so the key
 *  never reaches the client. Response bodies stream through (SSE-safe). */
import { env } from '$env/dynamic/private';
import type { RequestHandler } from './$types';

const BASE = () => env.MARQUEE_API_URL ?? 'http://localhost:3165';

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

	const res = await fetch(target, init);

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
