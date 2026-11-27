import { isRedirect } from '@sveltejs/kit';
import { describe, expect, it, vi } from 'vitest';
import { SESSION_COOKIE } from '#lib/server/session.ts';

vi.mock('#lib/server/env.ts', () => ({ apiKey: () => 'the-api-key' }));

const { actions } = await import('./+page.server');

/** Sign in with the right key on a site whose address (ORIGIN) is `origin`; the cookie's
 *  options as the action set them. */
async function signInAt(origin: string, client: string) {
	const url = new URL(`${origin}/signin`);
	const body = new FormData();
	body.set('key', 'the-api-key');
	const set = vi.fn();
	try {
		await actions.default({
			request: new Request(url, { method: 'POST', body }),
			cookies: { set },
			url,
			getClientAddress: () => client
		} as never);
	} catch (err) {
		if (!isRedirect(err)) throw err;
	}
	expect(set).toHaveBeenCalledOnce();
	const [name, , options] = set.mock.calls[0]!;
	expect(name).toBe(SESSION_COOKIE);
	return options as Record<string, unknown>;
}

describe('the session cookie', () => {
	it('is Secure when the site is served over https (behind a reverse proxy)', async () => {
		const options = await signInAt('https://marquee.example.org', '192.0.2.1');
		expect(options).toMatchObject({ secure: true, httpOnly: true, sameSite: 'strict' });
	});

	it('is not Secure over plain HTTP, where a browser would never send it back', async () => {
		const options = await signInAt('http://marquee.example.lan:3000', '192.0.2.2');
		expect(options).toMatchObject({ secure: false, httpOnly: true, sameSite: 'strict' });
	});
});
