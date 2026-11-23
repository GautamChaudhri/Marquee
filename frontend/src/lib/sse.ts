/** Temporary compact-snapshot reconciliation used until Chunk 3 supplies the
 * multiplexed durable event stream. Per-job streaming contracts were removed
 * in JMC2C; callers retain their existing callback shape without opening SSE.
 */
import { browser } from '$app/environment';

export function subscribe(
	path: string,
	types: string[],
	callback: (type: string, data: unknown) => void
): () => void {
	if (!browser) return () => {};
	let stopped = false;
	let inFlight = false;
	let timer: ReturnType<typeof setTimeout> | null = null;

	const poll = async () => {
		if (stopped || inFlight) return;
		inFlight = true;
		try {
			const response = await fetch(path);
			if (!response.ok) return;
			const snapshot = (await response.json()) as Record<string, unknown>;
			const phase = typeof snapshot.phase === 'string' ? snapshot.phase : 'running';
			const outcome = typeof snapshot.outcome === 'string' ? snapshot.outcome : null;
			callback('message', {
				state: outcome ?? phase,
				detail: snapshot.progress ?? {},
				snapshot
			});
			if (phase === 'terminal') {
				callback('done', snapshot);
				stopped = true;
			}
		} catch {
			// Preserve the last-good UI state; the next bounded poll repairs it.
		} finally {
			inFlight = false;
			if (!stopped) timer = setTimeout(() => void poll(), 2000);
		}
	};

	void types;
	void poll();
	return () => {
		stopped = true;
		if (timer) clearTimeout(timer);
	};
}
