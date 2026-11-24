/** Sign-in with the API key, and the session cookie it sets.
 *
 *  The cookie holds an expiry time and an HMAC-SHA256 of it, keyed by a value derived from
 *  the API key. There is no session store: changing the API key signs everyone out, and a
 *  cookie proves only that whoever holds it once knew the key. */
import { createHash, createHmac, timingSafeEqual } from 'node:crypto';

export const SESSION_COOKIE = 'marquee_session';
export const SESSION_SECONDS = 30 * 24 * 60 * 60;
const PURPOSE = 'marquee web session v1';
const VALUE = /^(\d{1,12})\.([A-Za-z0-9_-]{43})$/;

function sessionSecret(apiKey: string): Buffer {
	return createHmac('sha256', apiKey).update(PURPOSE).digest();
}

function sign(expires: number, apiKey: string): Buffer {
	return createHmac('sha256', sessionSecret(apiKey)).update(String(expires)).digest();
}

/** The cookie's value for a session that ends SESSION_SECONDS after `nowMs`. */
export function createSession(apiKey: string, nowMs: number): string {
	const expires = Math.floor(nowMs / 1000) + SESSION_SECONDS;
	return `${expires}.${sign(expires, apiKey).toString('base64url')}`;
}

/** Whether `value` is a session this key signed that has not expired. */
export function verifySession(
	value: string | undefined | null,
	apiKey: string | null,
	nowMs: number
): boolean {
	if (!value || !apiKey) return false;
	const match = VALUE.exec(value);
	if (!match) return false;
	const expires = Number(match[1]);
	if (!Number.isSafeInteger(expires) || expires <= Math.floor(nowMs / 1000)) return false;
	const given = Buffer.from(match[2]!, 'base64url');
	const expected = sign(expires, apiKey);
	return given.length === expected.length && timingSafeEqual(given, expected);
}

/** Whether the key someone typed is the configured one, compared in constant time. Both
 *  sides are hashed first, so the comparison does not reveal the key's length either. */
export function keyMatches(given: string, apiKey: string): boolean {
	const a = createHash('sha256').update(given, 'utf8').digest();
	const b = createHash('sha256').update(apiKey, 'utf8').digest();
	return timingSafeEqual(a, b);
}

/** Where to go after signing in: a path on this site, never another site. */
export function safeNext(next: string | null): string {
	if (!next || !next.startsWith('/') || next.startsWith('//') || next.startsWith('/\\')) {
		return '/';
	}
	return next.startsWith('/signin') ? '/' : next;
}
