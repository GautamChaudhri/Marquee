import { describe, expect, it } from 'vitest';
import { isSafeMethod, isSameOrigin } from './origin';

const URL_HERE = new URL('http://localhost:3000/api/sync');

function post(headers: Record<string, string>): Request {
	return new Request(URL_HERE, { method: 'POST', headers });
}

describe('the same-origin check', () => {
	it('knows which methods change nothing', () => {
		expect(['GET', 'HEAD', 'OPTIONS', 'get'].every(isSafeMethod)).toBe(true);
		expect(['POST', 'PUT', 'PATCH', 'DELETE'].some(isSafeMethod)).toBe(false);
	});

	it('accepts a request from a page of this origin', () => {
		expect(isSameOrigin(post({ origin: 'http://localhost:3000' }), URL_HERE)).toBe(true);
		expect(
			isSameOrigin(
				post({ origin: 'http://localhost:3000', 'sec-fetch-site': 'same-origin' }),
				URL_HERE
			)
		).toBe(true);
		expect(isSameOrigin(post({ 'sec-fetch-site': 'same-origin' }), URL_HERE)).toBe(true);
	});

	it('refuses another origin, a cross-site fetch, and a request with no evidence', () => {
		const cases: Record<string, string>[] = [
			{},
			{ origin: 'http://attacker.test' },
			{ origin: 'https://localhost:3000' },
			{ origin: 'null' },
			{ origin: 'http://localhost:3000', 'sec-fetch-site': 'cross-site' },
			{ 'sec-fetch-site': 'same-site' }
		];
		for (const headers of cases) {
			expect(isSameOrigin(post(headers), URL_HERE)).toBe(false);
		}
	});
});
