import { describe, expect, it } from 'vitest';
import { SESSION_SECONDS, createSession, keyMatches, safeNext, verifySession } from './session';

const KEY = 'the-api-key';
const NOW = Date.UTC(2026, 10, 24, 12, 0, 0);

describe('the session cookie', () => {
	it('accepts a cookie it signed with this key while it has not expired', () => {
		const value = createSession(KEY, NOW);
		expect(verifySession(value, KEY, NOW)).toBe(true);
		expect(verifySession(value, KEY, NOW + (SESSION_SECONDS - 1) * 1000)).toBe(true);
		expect(value).toMatch(/^\d+\.[A-Za-z0-9_-]{43}$/);
		expect(value).not.toContain(KEY);
	});

	it('refuses an expired cookie', () => {
		const value = createSession(KEY, NOW);
		expect(verifySession(value, KEY, NOW + SESSION_SECONDS * 1000)).toBe(false);
	});

	it('refuses a tampered cookie', () => {
		const value = createSession(KEY, NOW);
		const [expires, mac] = value.split('.') as [string, string];
		const later = `${Number(expires) + 86_400}.${mac}`;
		const flipped = `${expires}.${mac[0] === 'A' ? 'B' : 'A'}${mac.slice(1)}`;
		for (const bad of [later, flipped, `${value}x`, value.slice(0, -1), '', 'nonsense']) {
			expect(verifySession(bad, KEY, NOW)).toBe(false);
		}
		expect(verifySession(undefined, KEY, NOW)).toBe(false);
	});

	it('refuses a cookie signed with another key, and any cookie when no key is set', () => {
		const value = createSession('another-key', NOW);
		expect(verifySession(value, KEY, NOW)).toBe(false);
		expect(verifySession(createSession(KEY, NOW), null, NOW)).toBe(false);
	});
});

describe('the key typed at sign-in', () => {
	it('matches only the configured key', () => {
		expect(keyMatches(KEY, KEY)).toBe(true);
		for (const wrong of ['', 'the-api-ke', 'the-api-key ', 'THE-API-KEY', 'x'.repeat(500)]) {
			expect(keyMatches(wrong, KEY)).toBe(false);
		}
	});
});

describe('where sign-in goes next', () => {
	it('keeps a path of this site, with its query and fragment', () => {
		expect(safeNext('/movies/3?x=1')).toBe('/movies/3?x=1');
		expect(safeNext('/activity')).toBe('/activity');
		expect(safeNext('/review/12?from=queue#panel')).toBe('/review/12?from=queue#panel');
		expect(safeNext('/movies/../activity')).toBe('/activity');
	});

	it('never leaves this site', () => {
		for (const away of [
			null,
			'',
			'x',
			'https://elsewhere.test/',
			'http://localhost:3000/movies/3',
			'//elsewhere.test',
			'///elsewhere.test',
			'/\\elsewhere.test',
			'/\\/elsewhere.test',
			'\\\\elsewhere.test',
			'/\t/elsewhere.test',
			'/\t\t/elsewhere.test',
			'/\n/elsewhere.test',
			'/\r/elsewhere.test',
			'/movies/3\r\nSet-Cookie: x=1',
			'/\u0000/elsewhere.test',
			'/\u007f/elsewhere.test',
			'/\u0085/elsewhere.test',
			'javascript:alert(1)',
			'/.//elsewhere.test',
			'/..//elsewhere.test',
			'/x/..//elsewhere.test'
		]) {
			const next = safeNext(away);
			expect.soft(next, JSON.stringify(away)).toBe('/');
		}
	});

	it('does not go back to sign-in', () => {
		expect(safeNext('/signin')).toBe('/');
		expect(safeNext('/signin?next=/')).toBe('/');
		expect(safeNext('/signin/')).toBe('/');
	});

	it('gives only an answer a browser resolves to this site', () => {
		const base = 'http://localhost:3000';
		for (const given of ['/%09/elsewhere.test', '/%2F%2Felsewhere.test', '/a//b', '/movies/3#x']) {
			const next = safeNext(given);
			expect(next.startsWith('/') && !next.startsWith('//'), given).toBe(true);
			expect(new URL(next, base).origin, given).toBe(base);
			expect(
				[...next].some((c) => c.charCodeAt(0) < 0x20 || c === '\\'),
				given
			).toBe(false);
		}
	});
});
