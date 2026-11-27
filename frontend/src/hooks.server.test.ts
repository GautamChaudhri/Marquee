import { isRedirect } from '@sveltejs/kit';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SESSION_COOKIE, createSession, safeNext } from '#lib/server/session.ts';

const settings = vi.hoisted(() => ({ key: 'the-api-key' as string | null }));
vi.mock('#lib/server/env.ts', () => ({
	apiKey: () => settings.key,
	apiUrl: () => 'http://backend.test:8000'
}));

const { handle } = await import('./hooks.server');

function event(
	path: string,
	init: RequestInit = {},
	cookie?: string,
	origin = 'http://localhost:3000'
) {
	const url = new URL(`${origin}${path}`);
	return {
		url,
		request: new Request(url, init),
		cookies: { get: (name: string) => (name === SESSION_COOKIE ? cookie : undefined) }
	};
}

async function run(path: string, init: RequestInit = {}, cookie?: string, origin?: string) {
	const resolve = vi.fn().mockResolvedValue(new Response('page'));
	try {
		const response = (await handle({
			event: event(path, init, cookie, origin),
			resolve
		} as never)) as Response;
		return { response, resolve, redirect: null };
	} catch (err) {
		if (isRedirect(err)) return { response: null, resolve, redirect: err };
		throw err;
	}
}

describe('the hooks', () => {
	beforeEach(() => {
		settings.key = 'the-api-key';
	});

	it('sends HSTS when the site is served over https, and only then', async () => {
		const https = await run('/signin', {}, undefined, 'https://marquee.example.org');
		expect(https.response?.headers.get('strict-transport-security')).toBe('max-age=31536000');
		expect(https.response?.headers.get('x-frame-options')).toBe('DENY');
		const refused = await run('/api/movies', {}, undefined, 'https://marquee.example.org');
		expect(refused.response?.status).toBe(401);
		expect(refused.response?.headers.get('strict-transport-security')).toBe('max-age=31536000');
		const http = await run('/signin');
		expect(http.response?.headers.get('strict-transport-security')).toBeNull();
		expect(http.response?.headers.get('x-content-type-options')).toBe('nosniff');
	});

	it('serves the sign-in page to anyone', async () => {
		const { response, resolve } = await run('/signin');
		expect(resolve).toHaveBeenCalledOnce();
		expect(response?.headers.get('x-frame-options')).toBe('DENY');
	});

	it('sends a page without a session to sign-in, remembering where it was going', async () => {
		const { redirect, resolve } = await run('/movies/3?tab=runs');
		expect(resolve).not.toHaveBeenCalled();
		expect(redirect?.status).toBe(303);
		expect(redirect?.location).toBe('/signin?next=%2Fmovies%2F3%3Ftab%3Druns');
		expect((await run('/')).redirect?.location).toBe('/signin');
	});

	it("sends any odd path to this site's sign-in, and sign-in back to this site", async () => {
		const base = 'http://localhost:3000';
		for (const path of [
			'//elsewhere.test/x',
			'/%09/elsewhere.test',
			'/\\elsewhere.test',
			'/.//x?a=%0a'
		]) {
			const { redirect } = await run(path);
			expect(redirect?.status, path).toBe(303);
			const location = new URL(redirect!.location, base);
			expect(location.origin, path).toBe(base);
			expect(location.pathname, path).toBe('/signin');
			const after = new URL(safeNext(location.searchParams.get('next')), base);
			expect(after.origin, path).toBe(base);
		}
	});

	it('answers /api without a session with 401, and with a bad or expired one too', async () => {
		const stale = createSession('the-api-key', Date.now() - 31 * 86_400_000);
		for (const cookie of [undefined, 'forged.cookie', stale, createSession('other', Date.now())]) {
			const { response, resolve } = await run('/api/movies', {}, cookie);
			expect(response?.status).toBe(401);
			expect(await response?.json()).toEqual({ detail: 'Sign in first.' });
			expect(resolve).not.toHaveBeenCalled();
		}
	});

	it('lets a signed-in request through', async () => {
		const cookie = createSession('the-api-key', Date.now());
		for (const path of ['/', '/activity', '/api/worker']) {
			const { resolve } = await run(path, {}, cookie);
			expect(resolve).toHaveBeenCalledOnce();
		}
	});

	it('refuses a cross-origin change with 403, signed in or not', async () => {
		const cookie = createSession('the-api-key', Date.now());
		const attack = { method: 'POST', headers: { origin: 'http://attacker.test' } };
		const api = await run('/api/sync', attack, cookie);
		expect(api.response?.status).toBe(403);
		expect(api.resolve).not.toHaveBeenCalled();
		const signin = await run('/signin', attack);
		expect(signin.response?.status).toBe(403);
		const ours = await run(
			'/api/sync',
			{ method: 'POST', headers: { origin: 'http://localhost:3000' } },
			cookie
		);
		expect(ours.resolve).toHaveBeenCalledOnce();
	});

	it('with no key configured serves only the sign-in page', async () => {
		settings.key = null;
		expect((await run('/signin')).resolve).toHaveBeenCalledOnce();
		expect((await run('/activity')).redirect?.location).toBe('/signin');
		const api = await run('/api/movies');
		expect(api.response?.status).toBe(503);
	});
});
