import { afterEach, describe, expect, it, vi } from 'vitest';
import { call, createApi, signInOnUnauthorized, signInUrl } from './client';

afterEach(() => {
	vi.unstubAllGlobals();
});

function respond(status: number, body: unknown): typeof fetch {
	return vi.fn(async () => Response.json(body, { status })) as unknown as typeof fetch;
}

describe('the API client', () => {
	it('calls this site’s own /api and folds the answer into a result', async () => {
		const upstream = respond(200, {
			running: true,
			since: null,
			queued: 2,
			running_jobs: 0,
			failed: 0
		});
		const api = createApi({ baseUrl: 'http://localhost:3000', fetch: upstream });
		const result = await call(api.GET('/api/worker'));
		expect(result).toEqual({
			ok: true,
			status: 200,
			data: { running: true, since: null, queued: 2, running_jobs: 0, failed: 0 }
		});
		const request = (upstream as unknown as ReturnType<typeof vi.fn>).mock.calls[0]![0] as Request;
		expect(request.url).toBe('http://localhost:3000/api/worker');
		expect(request.headers.has('x-api-key')).toBe(false);
	});

	it('turns a refusal into a readable message', async () => {
		const api = createApi({
			baseUrl: 'http://localhost:3000',
			fetch: respond(409, { detail: 'movie 3 (Heat) has no TMDB id' })
		});
		const result = await call(
			api.POST('/api/movies/{movie_id}/run', { params: { path: { movie_id: 3 } } })
		);
		expect(result).toEqual({ ok: false, status: 409, message: 'Movie 3 (Heat) has no TMDB id.' });
	});

	it('turns a network failure into a result, not a throw', async () => {
		const broken = vi.fn(async () => {
			throw new TypeError('fetch failed');
		}) as unknown as typeof fetch;
		const api = createApi({ baseUrl: 'http://localhost:3000', fetch: broken });
		const result = await call(api.GET('/api/worker'));
		expect(result.ok).toBe(false);
		expect(result.ok ? '' : result.message).toMatch(/not reachable/);
	});
});

describe('a 401', () => {
	it('sends the browser to sign-in, then back where it was', async () => {
		vi.stubGlobal('window', { location: { pathname: '/movies/4', search: '?tab=x' } });
		const go = vi.fn();
		const middleware = signInOnUnauthorized(go);
		await middleware.onResponse!({ response: new Response(null, { status: 401 }) } as never);
		expect(go).toHaveBeenCalledWith('/signin?next=%2Fmovies%2F4%3Ftab%3Dx');
		await middleware.onResponse!({ response: new Response(null, { status: 404 }) } as never);
		expect(go).toHaveBeenCalledOnce();
	});

	it('goes to plain sign-in from the library', () => {
		expect(signInUrl({ pathname: '/', search: '' })).toBe('/signin');
	});
});
