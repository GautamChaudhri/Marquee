/** Per-run / per-job SSE. Marquee has NO global event stream — you open an
 *  EventSource for a single run or job and close it when done (design/MARQUEE_API.md §4).
 *  EventSource can't send headers, so this relies on the same-origin proxy
 *  (loopback/DEBUG) for auth.
 *
 *  Used by later slices (Pipeline run, Letterbox/media jobs). Foundation stub. */
import { browser } from '$app/environment';

export type SseHandler = (event: string, data: unknown) => void;

/** Subscribe to an event-stream endpoint (e.g. `/api/pipeline/runs/<id>/events`).
 *  Returns an unsubscribe function.
 *
 *  An `'error'` pseudo-event is dispatched to the handler if the connection
 *  drops so callers can surface a transient "reconnecting" state. The native
 *  EventSource auto-reconnects; the returned unsubscribe always closes the
 *  socket so it can never silently wedge a browser connection slot. */
export function subscribe(path: string, types: string[], onEvent: SseHandler): () => void {
	if (!browser) return () => {};
	const es = new EventSource(path);
	const listeners = types.map((type): [string, (e: MessageEvent) => void] => {
		const fn = (e: MessageEvent) => {
			let parsed: unknown = e.data;
			try {
				parsed = JSON.parse(e.data);
			} catch {
				/* leave as raw string */
			}
			onEvent(type, parsed);
		};
		es.addEventListener(type, fn);
		return [type, fn];
	});
	const onError = () => onEvent('error', { readyState: es.readyState });
	es.addEventListener('error', onError);
	return () => {
		for (const [type, fn] of listeners) es.removeEventListener(type, fn);
		es.removeEventListener('error', onError);
		es.close();
	};
}
