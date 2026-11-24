/** `/api/*` on this server: forwarded to the backend with the API key (see
 *  `$lib/server/proxy`). The session and same-origin checks run in `hooks.server.ts` first. */
import { apiKey, apiUrl } from '$lib/server/env';
import { forward } from '$lib/server/proxy';
import type { RequestHandler } from './$types';

const PREFIX = '/api/';

const handler: RequestHandler = async ({ request, url, fetch }) => {
	const key = apiKey();
	if (!key) {
		return Response.json({ detail: 'The web server has no API key configured.' }, { status: 503 });
	}
	// The path as the browser sent it, still percent-encoded, and only ever under /api/.
	const path = url.pathname.slice(PREFIX.length);
	const base = apiUrl();
	const target = new URL(`${base}${PREFIX}${path}`);
	if (!target.pathname.startsWith(PREFIX) || target.origin !== new URL(base).origin) {
		return Response.json({ detail: 'Not found' }, { status: 404 });
	}
	return forward(request, path, url.search, { apiUrl: base, apiKey: key, fetch });
};

export const GET = handler;
export const POST = handler;
export const PUT = handler;
export const PATCH = handler;
export const DELETE = handler;
