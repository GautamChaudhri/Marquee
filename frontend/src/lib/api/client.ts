/** The typed API client: openapi-fetch over the types generated from `frontend/openapi.json`,
 *  calling this site's own `/api`, which the web server forwards with the key.
 *
 *  `call()` turns every answer into an `ApiResult`, so a page handles success and failure in
 *  one shape and never sees a thrown network error or a raw error body. A 401 sends the
 *  browser back to sign-in; this is the one place that does. */
import createClient, { type Client, type Middleware } from 'openapi-fetch';
import { messageFor } from './errors';
import type { paths } from './generated/schema';

export type ApiResult<T> =
	{ ok: true; status: number; data: T } | { ok: false; status: number; message: string };

/** Where a 401 sends the browser: sign-in, then back to the page it was on. */
export function signInUrl(location: { pathname: string; search: string }): string {
	const here = `${location.pathname}${location.search}`;
	return here === '/' ? '/signin' : `/signin?next=${encodeURIComponent(here)}`;
}

export function signInOnUnauthorized(go: (url: string) => void): Middleware {
	return {
		onResponse({ response }) {
			if (response.status === 401 && typeof window !== 'undefined') {
				go(signInUrl(window.location));
			}
			return undefined;
		}
	};
}

export function createApi(options: { baseUrl?: string; fetch?: typeof fetch } = {}): Client<paths> {
	const client = createClient<paths>({ baseUrl: options.baseUrl ?? '', fetch: options.fetch });
	client.use(signInOnUnauthorized((url) => window.location.assign(url)));
	return client;
}

export const api = createApi();

/** Await an openapi-fetch call and fold it into an `ApiResult`. */
export async function call<T>(
	request: Promise<{ data?: T; error?: unknown; response: Response }>
): Promise<ApiResult<T>> {
	try {
		const { data, error, response } = await request;
		if (response.ok && data !== undefined) return { ok: true, status: response.status, data };
		return { ok: false, status: response.status, message: messageFor(response.status, error) };
	} catch (err) {
		return { ok: false, status: 0, message: messageFor(0, err) };
	}
}
