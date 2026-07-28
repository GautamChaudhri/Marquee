/**
 * A v4 UUID that also works outside a secure context.
 *
 * `crypto.randomUUID` is secure-context-only: browsers expose it on `https://` and
 * `localhost` and nowhere else. This app is normally reached over plain HTTP at a LAN
 * address, where calling it throws `crypto.randomUUID is not a function` — and every
 * caller here builds an idempotency key, so the throw happens before the request is
 * ever sent and the mutation silently never reaches the server.
 *
 * `crypto.getRandomValues` is not secure-context-gated, so derive the UUID from it and
 * keep the exact shape the API expects. The `Math.random` branch is a last resort for
 * environments with no Web Crypto at all; it is not cryptographically strong, which is
 * fine — these values only need to be unique, not unguessable.
 */
export function randomUuid(): string {
	const webCrypto = globalThis.crypto as Crypto | undefined;
	if (typeof webCrypto?.randomUUID === 'function') return webCrypto.randomUUID();

	const bytes = new Uint8Array(16);
	if (typeof webCrypto?.getRandomValues === 'function') {
		webCrypto.getRandomValues(bytes);
	} else {
		for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256);
	}
	bytes[6] = (bytes[6]! & 0x0f) | 0x40; // version 4
	bytes[8] = (bytes[8]! & 0x3f) | 0x80; // variant 10xx

	const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
	return [
		hex.slice(0, 8),
		hex.slice(8, 12),
		hex.slice(12, 16),
		hex.slice(16, 20),
		hex.slice(20)
	].join('-');
}
