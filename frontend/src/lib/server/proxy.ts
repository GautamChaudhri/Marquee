/** The same-origin proxy: the browser calls `/api/*` on this server, which forwards the
 *  request to the backend with the API key attached, so the key never reaches the browser.
 *
 *  It drops hop-by-hop headers, the browser's cookies, and any credential the browser sent;
 *  limits a request body to 1 MB; gives up after 20 s; and streams the answer back with its
 *  status, ETag, and Cache-Control unchanged (a 304 stays a 304). The same-origin check for
 *  requests that change something happens in `hooks.server.ts`, before this runs. */

export const PROXY_TIMEOUT_MS = 20_000;
export const MAX_REQUEST_BODY_BYTES = 1_048_576;

/** RFC 9110 section 7.6.1, and the headers this proxy sets or must never pass on. */
const DROPPED_REQUEST_HEADERS = [
	'connection',
	'keep-alive',
	'proxy-authenticate',
	'proxy-authorization',
	'proxy-connection',
	'te',
	'trailer',
	'transfer-encoding',
	'upgrade',
	'host',
	'content-length',
	'cookie',
	'authorization',
	'x-api-key',
	'origin',
	'referer'
];
const DROPPED_RESPONSE_HEADERS = [
	'connection',
	'keep-alive',
	'proxy-authenticate',
	'proxy-connection',
	'trailer',
	'transfer-encoding',
	'upgrade',
	'set-cookie'
];

export interface ProxyTarget {
	apiUrl: string;
	apiKey: string;
	fetch: typeof fetch;
}

function problem(status: number, detail: string): Response {
	return Response.json({ detail }, { status });
}

/** The header names a `Connection` header lists are hop-by-hop too. */
function withoutHopByHop(headers: Headers, names: string[]): Headers {
	const out = new Headers(headers);
	const listed = (headers.get('connection') ?? '')
		.split(',')
		.map((name) => name.trim().toLowerCase())
		.filter(Boolean);
	for (const name of [...names, ...listed]) out.delete(name);
	return out;
}

function declaredLength(request: Request): number | null | 'invalid' {
	const value = request.headers.get('content-length');
	if (value === null) return null;
	return /^\d+$/.test(value) ? Number(value) : 'invalid';
}

/** Forward `request` for `/api/<path><search>` to the backend and return its answer. */
export async function forward(
	request: Request,
	path: string,
	search: string,
	target: ProxyTarget
): Promise<Response> {
	const declared = declaredLength(request);
	if (declared === 'invalid') return problem(400, 'The request has a malformed Content-Length.');
	if (declared !== null && declared > MAX_REQUEST_BODY_BYTES) {
		return problem(413, 'The request body is larger than 1 MB.');
	}

	const headers = withoutHopByHop(request.headers, DROPPED_REQUEST_HEADERS);
	headers.set('x-api-key', target.apiKey);

	const abort = new AbortController();
	const init: RequestInit & { duplex?: 'half' } = {
		method: request.method,
		headers,
		redirect: 'manual',
		signal: AbortSignal.any([abort.signal, AbortSignal.timeout(PROXY_TIMEOUT_MS)])
	};
	// The flag, not the error's type, says a body was too large: aborting the stream can
	// surface as an AbortError.
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
						controller.error(new Error('request body too large'));
						return;
					}
					controller.enqueue(chunk);
				}
			})
		);
		// A streamed body is sent chunked; undici requires `duplex: 'half'` for it.
		init.duplex = 'half';
	}

	let upstream: Response;
	try {
		upstream = await target.fetch(`${target.apiUrl}/api/${path}${search}`, init);
	} catch (err) {
		if (tooLarge) return problem(413, 'The request body is larger than 1 MB.');
		if (err instanceof DOMException && err.name === 'TimeoutError') {
			return problem(504, 'The API did not answer within 20 seconds.');
		}
		return problem(502, 'The API is not reachable.');
	}

	const responseHeaders = withoutHopByHop(upstream.headers, DROPPED_RESPONSE_HEADERS);
	// fetch() decompressed the body only if the backend compressed it; then the length and
	// encoding no longer describe what is sent on.
	if (responseHeaders.has('content-encoding')) {
		responseHeaders.delete('content-encoding');
		responseHeaders.delete('content-length');
	}
	// A null-body status (304, 204) must not carry a body stream.
	const body = [204, 205, 304].includes(upstream.status) ? null : upstream.body;
	return new Response(body, { status: upstream.status, headers: responseHeaders });
}
