import { describe, expect, it, vi } from 'vitest';
import { POST } from './+server';

async function mutate(headers: HeadersInit, upstream = vi.fn()) {
	const url = new URL('https://marquee.test/api/settings/config');
	const request = new Request(url, {
		method: 'POST',
		headers,
		body: '{}'
	});
	const response = await POST({
		request,
		params: { path: 'settings/config' },
		url,
		fetch: upstream
	} as never);
	return { response, upstream };
}

describe('same-origin mutation proxy', () => {
	it('rejects missing and mismatched Origin evidence before reading or forwarding a body', async () => {
		const cases: HeadersInit[] = [
			{},
			{ origin: 'https://attacker.test' },
			{ origin: 'https://marquee.test', 'sec-fetch-site': 'cross-site' }
		];
		for (const headers of cases) {
			const { response, upstream } = await mutate(headers);
			expect(response.status).toBe(403);
			expect(await response.json()).toMatchObject({ code: 'cross_origin_mutation' });
			expect(upstream).not.toHaveBeenCalled();
		}
	});

	it('forwards an exact-origin browser mutation', async () => {
		const upstream = vi.fn().mockResolvedValue(Response.json({ ok: true }));
		const { response } = await mutate(
			{ origin: 'https://marquee.test', 'sec-fetch-site': 'same-origin' },
			upstream
		);
		expect(response.status).toBe(200);
		expect(upstream).toHaveBeenCalledOnce();
	});
});
