/** The web server's own settings, read at runtime from its environment.
 *
 *  `$app/env/private`, with both variables declared in `src/env.ts` as private and not static:
 *  a static variable is inlined into the build, and the built app must hold no key. Nothing
 *  here may reach page data, a public variable, or a client bundle. */
import { MARQUEE_API_URL, MARQUEE_API_KEY } from '$app/env/private';

export const DEFAULT_API_URL = 'http://127.0.0.1:8000';

/** Where the backend listens, without a trailing slash. */
export function apiUrl(): string {
	return (MARQUEE_API_URL?.trim() || DEFAULT_API_URL).replace(/\/+$/, '');
}

/** The backend's API key, or null when none is configured. */
export function apiKey(): string | null {
	const key = MARQUEE_API_KEY?.trim();
	return key ? key : null;
}
