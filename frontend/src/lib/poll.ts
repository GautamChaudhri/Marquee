/** Run `task` now and then again `intervalMs` after each run ends, until stopped. A run that
 *  is still going is never overlapped by the next, and a failure does not stop the loop. */
export function poll(task: () => unknown, intervalMs: number): () => void {
	let stopped = false;
	let timer: ReturnType<typeof setTimeout> | undefined;

	const run = async () => {
		if (stopped) return;
		try {
			await task();
		} catch {
			// The task reports its own failures; the loop goes on.
		}
		if (!stopped) timer = setTimeout(run, intervalMs);
	};
	void run();

	return () => {
		stopped = true;
		if (timer !== undefined) clearTimeout(timer);
	};
}
