/** Failed sign-ins: five within a minute from one address, and that address waits until
 *  the minute has passed. Held in memory; a restart forgets it. */
export const MAX_FAILURES = 5;
export const WINDOW_MS = 60_000;
const MAX_CLIENTS = 10_000;

export class FailureLimiter {
	readonly #failures = new Map<string, number[]>();

	constructor(
		readonly maxFailures = MAX_FAILURES,
		readonly windowMs = WINDOW_MS
	) {}

	#recent(client: string, nowMs: number): number[] {
		const kept = (this.#failures.get(client) ?? []).filter((t) => nowMs - t < this.windowMs);
		if (kept.length) this.#failures.set(client, kept);
		else this.#failures.delete(client);
		return kept;
	}

	/** Milliseconds until this client may try again, or 0 when it may try now. */
	retryAfterMs(client: string, nowMs: number): number {
		const recent = this.#recent(client, nowMs);
		if (recent.length < this.maxFailures) return 0;
		return recent[recent.length - this.maxFailures]! + this.windowMs - nowMs;
	}

	fail(client: string, nowMs: number): void {
		const recent = this.#recent(client, nowMs);
		if (!this.#failures.has(client) && this.#failures.size >= MAX_CLIENTS) {
			// Forget the oldest entry rather than grow without bound.
			const oldest = this.#failures.keys().next().value;
			if (oldest !== undefined) this.#failures.delete(oldest);
		}
		this.#failures.set(client, [...recent, nowMs]);
	}
}

export const signInFailures = new FailureLimiter();
