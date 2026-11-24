/** Every request passes here first.
 *
 *  - A request that changes something (anything but GET, HEAD, OPTIONS) must come from this
 *    site's own pages, or it is refused with 403.
 *  - Only the sign-in page is open. Every other page, and every `/api/*` request, needs a
 *    valid session cookie: a page without one goes to sign-in, `/api/*` answers 401.
 *  - With no API key configured, the sign-in page says so and nothing else is served.
 *
 *  Static assets (the built client files and `static/`) are served before this runs. */
import { redirect, type Handle } from '@sveltejs/kit';
import { apiKey } from '$lib/server/env';
import { isSafeMethod, isSameOrigin } from '$lib/server/origin';
import { SESSION_COOKIE, verifySession } from '$lib/server/session';

const OPEN_PATHS = new Set(['/signin', '/signin/']);
const SECURITY_HEADERS: Record<string, string> = {
	'x-content-type-options': 'nosniff',
	'x-frame-options': 'DENY',
	'referrer-policy': 'same-origin'
};

function isApi(pathname: string): boolean {
	return pathname === '/api' || pathname.startsWith('/api/');
}

function refuse(pathname: string, status: number, detail: string): Response {
	return isApi(pathname)
		? Response.json({ detail }, { status })
		: new Response(detail, { status, headers: { 'content-type': 'text/plain; charset=utf-8' } });
}

function withSecurityHeaders(response: Response): Response {
	for (const [name, value] of Object.entries(SECURITY_HEADERS)) {
		try {
			if (!response.headers.has(name)) response.headers.set(name, value);
		} catch {
			// A response with immutable headers keeps its own.
		}
	}
	return response;
}

export const handle: Handle = async ({ event, resolve }) => {
	const { pathname } = event.url;
	if (!isSafeMethod(event.request.method) && !isSameOrigin(event.request, event.url)) {
		return withSecurityHeaders(refuse(pathname, 403, 'Cross-site requests are not allowed.'));
	}
	if (!OPEN_PATHS.has(pathname)) {
		const key = apiKey();
		if (!key) {
			if (isApi(pathname)) {
				return withSecurityHeaders(
					refuse(pathname, 503, 'The web server has no API key configured.')
				);
			}
			redirect(303, '/signin');
		}
		if (!verifySession(event.cookies.get(SESSION_COOKIE), key, Date.now())) {
			if (isApi(pathname)) return withSecurityHeaders(refuse(pathname, 401, 'Sign in first.'));
			const next = `${pathname}${event.url.search}`;
			redirect(303, next === '/' ? '/signin' : `/signin?next=${encodeURIComponent(next)}`);
		}
	}
	return withSecurityHeaders(await resolve(event));
};
