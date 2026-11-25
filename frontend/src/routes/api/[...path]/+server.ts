/** `/api/*` on this server: forwarded to the backend with the API key (see
 *  `#lib/server/proxy`). The session and same-origin checks run in `hooks.server.ts` first. */
import { apiKey, apiUrl } from '#lib/server/env.ts';
import { forward } from '#lib/server/proxy.ts';
import type { RequestHandler } from './$types';

const PREFIX = '/api/';

const handler: RequestHandler = async ({ request, url }) => {
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
	// The plain fetch, not SvelteKit's: SvelteKit's adds the browser's cookies to a request
	// for the same host name, whatever the port, and the backend must never see them.
	return forward(request, path, url.search, { apiUrl: base, apiKey: key, fetch: globalThis.fetch });
};

export const GET = handler;
export const POST = handler;
export const PUT = handler;
export const PATCH = handler;
export const DELETE = handler;
