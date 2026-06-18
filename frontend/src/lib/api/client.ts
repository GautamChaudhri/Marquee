/** Thin fetch wrapper. Always called with SvelteKit's `fetch` from a `load()`
 *  or component, and always hits the same-origin proxy at `/api/*`
 *  (src/routes/api/[...path]/+server.ts), which injects the API key. */

export type Fetch = typeof globalThis.fetch;

export class ApiError extends Error {
	constructor(
		public status: number,
		message: string,
		public body?: unknown
	) {
		super(message);
		this.name = 'ApiError';
	}
}

function buildQuery(params?: Record<string, unknown>): string {
	if (!params) return '';
	const sp = new URLSearchParams();
	for (const [k, v] of Object.entries(params)) {
		if (v === undefined || v === null || v === '') continue;
		sp.set(k, String(v));
	}
	const s = sp.toString();
	return s ? `?${s}` : '';
}

async function parse(res: Response): Promise<unknown> {
	const ct = res.headers.get('content-type') ?? '';
	return ct.includes('application/json') ? res.json() : res.text();
}

export async function apiGet<T>(
	fetch: Fetch,
	path: string,
	params?: Record<string, unknown>
): Promise<T> {
	const res = await fetch(`/api${path}${buildQuery(params)}`);
	if (!res.ok) {
		throw new ApiError(
			res.status,
			`GET ${path} → ${res.status}`,
			await parse(res).catch(() => null)
		);
	}
	return (await res.json()) as T;
}

export async function apiSend<T>(
	fetch: Fetch,
	method: 'POST' | 'PUT' | 'PATCH' | 'DELETE',
	path: string,
	body?: unknown
): Promise<T> {
	const res = await fetch(`/api${path}`, {
		method,
		headers: body !== undefined ? { 'content-type': 'application/json' } : undefined,
		body: body !== undefined ? JSON.stringify(body) : undefined
	});
	if (!res.ok) {
		throw new ApiError(
			res.status,
			`${method} ${path} → ${res.status}`,
			await parse(res).catch(() => null)
		);
	}
	return (await res.json()) as T;
}
