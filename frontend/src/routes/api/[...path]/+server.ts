/** Same-origin → FastAPI proxy. The browser and `load()` call `/api/*`; this
 *  forwards to the backend with the server-only API key attached, so the key
 *  never reaches the client. Response bodies stream through (SSE-safe). */
import { env } from '$env/dynamic/private';
import type { RequestHandler } from './$types';

const BASE = () => env.MARQUEE_API_URL ?? 'http://localhost:3165';

// A hung backend request must not hold a browser connection slot forever — the
// browser caps ~6 per origin, so a few slow proxied calls (e.g. a 4K preview)
// freeze the whole UI until a manual refresh. Time out non-stream requests so a
// slow backend returns 504 fast and frees the slot. SSE is long-lived by design
// and exempted.
const PROXY_TIMEOUT_MS = 20000;
const MAX_REQUEST_BODY_BYTES = 1_048_576;

const BODY_TOO_LARGE = 'Request body exceeds the maximum allowed size.';
const BODY_BAD_LENGTH = 'Malformed or conflicting Content-Length header.';

// Mirror the backend envelope: { detail, code }.
function bodyError(status: number, code: string, detail: string): Response {
	return Response.json({ detail, code }, { status });
}

function declaredLength(request: Request): number | null | 'invalid' {
	const value = request.headers.get('content-length');
	if (value === null) return null;
	if (!/^\d+$/.test(value)) return 'invalid';
	return Number(value);
}

const handler: RequestHandler = async ({ request, params, url, fetch }) => {
	const target = `${BASE()}/api/${params.path}${url.search}`;

	const headers = new Headers(request.headers);
	headers.delete('host');
	headers.delete('connection');
	headers.delete('content-length');
	if (env.MARQUEE_API_KEY) headers.set('X-Api-Key', env.MARQUEE_API_KEY);

	const declared = declaredLength(request);
	if (declared === 'invalid') return bodyError(400, 'invalid_content_length', BODY_BAD_LENGTH);
	if (declared !== null && declared > MAX_REQUEST_BODY_BYTES) {
		return bodyError(413, 'request_body_too_large', BODY_TOO_LARGE);
	}

	const abort = new AbortController();
	// undici (Node ≥18) requires `duplex: 'half'` whenever a stream body is sent;
	// the type is not yet in every lib.dom, so extend it explicitly.
	const init: RequestInit & { duplex?: 'half' } = {
		method: request.method,
		headers,
		signal: abort.signal
	};
	// A flag — not the caught error's identity — is the authority for a
	// too-large body, since aborting the stream can surface as AbortError.
	let tooLarge = false;
	if (request.method !== 'GET' && request.method !== 'HEAD' && request.body) {
		let received = 0;
		init.body = request.body.pipeThrough(
			new TransformStream<Uint8Array, Uint8Array>({
				transform(chunk, controller) {
					received += chunk.byteLength;
					if (received > MAX_REQUEST_BODY_BYTES) {
						tooLarge = true;
						abort.abort();
						controller.error(new Error('request_body_too_large'));
						return;
					}
					controller.enqueue(chunk);
				}
			})
		);
		// Stream body ⇒ chunked transfer; content-length was already removed.
		init.duplex = 'half';
	}

	// Event streams (SSE) stay open indefinitely — never time them out.
	const isStream =
		params.path.endsWith('/events') || request.headers.get('accept') === 'text/event-stream';
	if (!isStream)
		init.signal = AbortSignal.any([abort.signal, AbortSignal.timeout(PROXY_TIMEOUT_MS)]);

	let res: Response;
	try {
		res = await fetch(target, init);
	} catch (err) {
		if (tooLarge) {
			return bodyError(413, 'request_body_too_large', BODY_TOO_LARGE);
		}
		if (err instanceof DOMException && err.name === 'TimeoutError') {
			return new Response('Upstream timed out', { status: 504 });
		}
		throw err;
	}

	const respHeaders = new Headers(res.headers);
	respHeaders.delete('content-encoding');
	respHeaders.delete('content-length');
	return new Response(res.body, { status: res.status, headers: respHeaders });
};

export const GET = handler;
export const POST = handler;
export const PUT = handler;
export const PATCH = handler;
export const DELETE = handler;
