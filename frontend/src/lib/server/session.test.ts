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
	it('stays on this site', () => {
		expect(safeNext('/movies/3?x=1')).toBe('/movies/3?x=1');
		expect(safeNext('/activity')).toBe('/activity');
		for (const away of [null, '', 'https://elsewhere.test/', '//elsewhere.test', '/\\x', 'x']) {
			expect(safeNext(away)).toBe('/');
		}
		expect(safeNext('/signin?next=/')).toBe('/');
	});
});
