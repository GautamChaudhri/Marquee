/** Thin fetch wrapper. Always called with SvelteKit's `fetch` from a `load()`
 *  or component, and always hits the same-origin proxy at `/api/*`
 *  (src/routes/api/[...path]/+server.ts), which injects the API key. */

import type { paths } from './generated/openapi';

export type Fetch = typeof globalThis.fetch;

type BackendPath = keyof paths & string;
type ClientPath<P extends string> = P extends `/api${infer Rest}` ? Rest : never;
type RuntimePath<P extends string> = P extends `${infer Head}{${string}}${infer Tail}`
	? `${Head}${string}${RuntimePath<Tail>}`
	: P;
type PathsWithMethod<M extends string> = {
	[P in BackendPath]: M extends keyof paths[P] ? P : never;
}[BackendPath];

type WithQuery<P extends string> = P | `${P}?${string}`;

export type ApiPathFor<M extends string> = WithQuery<RuntimePath<ClientPath<PathsWithMethod<M>>>>;
export type ApiGetPath = ApiPathFor<'get'>;
export type ApiMutationPath = ApiPathFor<'post' | 'put' | 'patch' | 'delete'>;

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

export function isConfigurationConflict(error: unknown): error is ApiError {
	if (!(error instanceof ApiError) || error.status !== 409) return false;
	const detail = (error.body as { detail?: { code?: string } } | undefined)?.detail;
	return detail?.code === 'configuration_version_conflict';
}

export const CONFIGURATION_CONFLICT_MESSAGE =
	'Settings changed in another session. Current values were reloaded; review your draft and save again.';

/** Default per-request timeout. A hung backend (e.g. one briefly saturated by
 *  a concurrent encode) must not hold a browser connection slot open forever —
 *  browsers cap ~6 per host, so a few stuck requests freeze the whole UI. We
 *  fail fast instead and let callers fall back / retry. */
const REQUEST_TIMEOUT_MS = 15000;

/** fetch with an AbortController timeout; rethrows as an ApiError on timeout. */
async function fetchWithTimeout(
	fetchFn: Fetch,
	url: string,
	init: RequestInit,
	path: string,
	method: string
): Promise<Response> {
	const controller = new AbortController();
	const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
	try {
		return await fetchFn(url, { ...init, signal: controller.signal });
	} catch (e) {
		if (e instanceof DOMException && e.name === 'AbortError') {
			throw new ApiError(0, `${method} ${path} → timed out after ${REQUEST_TIMEOUT_MS}ms`);
		}
		throw e;
	} finally {
		clearTimeout(timer);
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
	path: ApiGetPath,
	params?: Record<string, unknown>
): Promise<T> {
	const path_ = `${path}${buildQuery(params)}`;
	const res = await fetchWithTimeout(fetch, `/api${path_}`, {}, path, 'GET');
	if (!res.ok) {
		throw new ApiError(
			res.status,
			`GET ${path} → ${res.status}`,
			await parse(res).catch(() => null)
		);
	}
	return (await res.json()) as T;
}

export async function apiSend<
	T,
	M extends 'POST' | 'PUT' | 'PATCH' | 'DELETE' = 'POST' | 'PUT' | 'PATCH' | 'DELETE'
>(fetch: Fetch, method: M, path: ApiPathFor<Lowercase<M>>, body?: unknown): Promise<T> {
	const res = await fetchWithTimeout(
		fetch,
		`/api${path}`,
		{
			method,
			headers: body !== undefined ? { 'content-type': 'application/json' } : undefined,
			body: body !== undefined ? JSON.stringify(body) : undefined
		},
		path,
		method
	);
	if (!res.ok) {
		throw new ApiError(
			res.status,
			`${method} ${path} → ${res.status}`,
			await parse(res).catch(() => null)
		);
	}
	return (await res.json()) as T;
}
