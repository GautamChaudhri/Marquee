import { afterEach, describe, expect, it, vi } from 'vitest';
import { randomUuid } from './uuid';

const V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

describe('randomUuid', () => {
	afterEach(() => {
		vi.unstubAllGlobals();
	});

	it('returns a v4 uuid in a secure context', () => {
		expect(randomUuid()).toMatch(V4);
	});

	it('still works when crypto.randomUUID is absent', () => {
		// Plain HTTP on a LAN address: `randomUUID` is secure-context-only and simply
		// missing, but `getRandomValues` is not gated. Every idempotency key depends on
		// this, so an exception here means the mutation never leaves the browser.
		const { getRandomValues } = globalThis.crypto;
		vi.stubGlobal('crypto', { getRandomValues: getRandomValues.bind(globalThis.crypto) });

		const first = randomUuid();
		expect(first).toMatch(V4);
		expect(first).not.toBe(randomUuid());
	});

	it('falls back when Web Crypto is unavailable entirely', () => {
		vi.stubGlobal('crypto', undefined);
		expect(randomUuid()).toMatch(V4);
	});
});
