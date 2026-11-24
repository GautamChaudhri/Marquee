import { describe, expect, it, vi } from 'vitest';
import { MAX_REQUEST_BODY_BYTES, forward } from './proxy';

const KEY = 'server-side-key';
const API = 'http://backend.test:8000';

function target(upstream: typeof fetch) {
	return { apiUrl: API, apiKey: KEY, fetch: upstream };
}

function captured(upstream: ReturnType<typeof vi.fn>): { url: string; init: RequestInit } {
	const [url, init] = upstream.mock.calls[0] as [string, RequestInit];
	return { url, init };
}

describe('the proxy', () => {
	it('forwards to /api on the backend with the key and without the browser cookies', async () => {
		const upstream = vi.fn().mockResolvedValue(Response.json({ ok: true }));
		const request = new Request('http://localhost:3000/api/movies?page=2', {
			headers: {
				cookie: 'marquee_session=abc',
				authorization: 'Bearer from-the-browser',
				'x-api-key': 'from-the-browser',
				connection: 'keep-alive, x-hop',
				'x-hop': 'dropped',
				'keep-alive': 'timeout=5',
				accept: 'application/json',
				'if-none-match': '"abc-320"'
			}
		});
		const response = await forward(request, 'movies', '?page=2', target(upstream));

		expect(response.status).toBe(200);
		const { url, init } = captured(upstream);
		expect(url).toBe(`${API}/api/movies?page=2`);
		const sent = new Headers(init.headers);
		expect(sent.get('x-api-key')).toBe(KEY);
		for (const name of ['cookie', 'authorization', 'connection', 'keep-alive', 'x-hop', 'host']) {
			expect(sent.has(name)).toBe(false);
		}
		expect(sent.get('accept')).toBe('application/json');
		expect(sent.get('if-none-match')).toBe('"abc-320"');
	});

	it('passes ETag, Cache-Control, and a 304 through unchanged', async () => {
		const upstream = vi.fn().mockResolvedValue(
			new Response(null, {
				status: 304,
				headers: { etag: '"abc-320"', 'cache-control': 'private, max-age=31536000, immutable' }
			})
		);
		const response = await forward(
			new Request('http://localhost:3000/api/movies/1/poster?width=320'),
			'movies/1/poster',
			'?width=320',
			target(upstream)
		);
		expect(response.status).toBe(304);
		expect(response.body).toBeNull();
		expect(response.headers.get('etag')).toBe('"abc-320"');
		expect(response.headers.get('cache-control')).toBe('private, max-age=31536000, immutable');
	});

	it('streams the answer through and drops what the backend must not set', async () => {
		const upstream = vi.fn().mockResolvedValue(
			new Response('poster bytes', {
				status: 200,
				headers: {
					'content-type': 'image/jpeg',
					'set-cookie': 'x=1',
					'transfer-encoding': 'chunked'
				}
			})
		);
		const response = await forward(
			new Request('http://localhost:3000/api/movies/1/poster'),
			'movies/1/poster',
			'',
			target(upstream)
		);
		expect(await response.text()).toBe('poster bytes');
		expect(response.headers.get('content-type')).toBe('image/jpeg');
		expect(response.headers.has('set-cookie')).toBe(false);
	});

	it('forwards a small body and refuses one over 1 MB', async () => {
		const upstream = vi.fn().mockResolvedValue(Response.json({ id: 7 }, { status: 202 }));
		const small = new Request('http://localhost:3000/api/sync', { method: 'POST', body: '{}' });
		expect((await forward(small, 'sync', '', target(upstream))).status).toBe(202);
		expect(captured(upstream).init.method).toBe('POST');

		const declared = new Request('http://localhost:3000/api/sync', {
			method: 'POST',
			body: 'x',
			headers: { 'content-length': String(MAX_REQUEST_BODY_BYTES + 1) }
		});
		const tooBig = await forward(declared, 'sync', '', target(vi.fn()));
		expect(tooBig.status).toBe(413);
		expect(await tooBig.json()).toEqual({ detail: 'The request body is larger than 1 MB.' });

		const streamed = new Request('http://localhost:3000/api/sync', {
			method: 'POST',
			body: new Uint8Array(MAX_REQUEST_BODY_BYTES + 10)
		});
		const reading = vi.fn(async (_url: string, init: RequestInit) => {
			await new Response(init.body).arrayBuffer();
			return Response.json({});
		});
		const refused = await forward(streamed, 'sync', '', target(reading as unknown as typeof fetch));
		expect(refused.status).toBe(413);
	});

	it('answers 504 when the backend is slow and 502 when it is not there', async () => {
		const timeout = vi.fn().mockRejectedValue(new DOMException('slow', 'TimeoutError'));
		const slow = await forward(
			new Request('http://localhost:3000/api/worker'),
			'worker',
			'',
			target(timeout)
		);
		expect(slow.status).toBe(504);
		expect(await slow.json()).toEqual({ detail: 'The API did not answer within 20 seconds.' });

		const refused = vi.fn().mockRejectedValue(new TypeError('fetch failed'));
		const down = await forward(
			new Request('http://localhost:3000/api/worker'),
			'worker',
			'',
			target(refused)
		);
		expect(down.status).toBe(502);
		expect(await down.json()).toEqual({ detail: 'The API is not reachable.' });
	});

	it('refuses a malformed Content-Length', async () => {
		const request = new Request('http://localhost:3000/api/sync', {
			method: 'POST',
			body: '{}',
			headers: { 'content-length': '12abc' }
		});
		expect((await forward(request, 'sync', '', target(vi.fn()))).status).toBe(400);
	});
});
