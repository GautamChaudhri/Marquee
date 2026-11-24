/** The web server's own settings, read at runtime from its environment.
 *
 *  `$env/dynamic/private`, never `$env/static/private`: a static import is inlined into the
 *  build, and the built app must hold no key. Nothing here may reach page data, a `PUBLIC_`
 *  variable, or a client bundle. */
import { env } from '$env/dynamic/private';

export const DEFAULT_API_URL = 'http://127.0.0.1:8000';

/** Where the backend listens, without a trailing slash. */
export function apiUrl(): string {
	return (env.MARQUEE_API_URL?.trim() || DEFAULT_API_URL).replace(/\/+$/, '');
}

/** The backend's API key, or null when none is configured. */
export function apiKey(): string | null {
	const key = env.MARQUEE_API_KEY?.trim();
	return key ? key : null;
}
