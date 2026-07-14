/** Track a durable job to completion from the canonical compact snapshot.
 *
 *  The snapshot poll (`getJob`) guarantees the bar moves even when the SSE
 *  opened right after a POST misses the first events; the SSE (`/jobs/{id}/events`,
 *  which replays on connect) fills in fine-grained per-stage / per-movie detail
 *  between polls. Either path can finalize the job. Mirrors
 *  `routes/letterbox/+page.svelte`. */
import type { Fetch } from './api/client';
import { getJob, isTerminal, type JobSnapshot } from './api/jobs';

/** The (loosely-typed) progress detail the pipeline bridge writes onto
 *  `job.progress` and each `JobEvent.detail`. */
export interface JobProgressDetail {
	stage?: string;
	state?: string;
	done?: number;
	total?: number;
	survivors?: number;
	movie_index?: number;
	movie_total?: number;
	movies_done?: number;
	movie_id?: number;
	title?: string;
	/** Operation-specific narration ("Removing English subtitle (SDH) — mkvmerge"). */
	message?: string;
	[k: string]: unknown;
}

export interface TrackHandlers<T = JobSnapshot> {
	onProgress?: (p: { status: string; detail: JobProgressDetail }) => void;
	onDone?: (job: T) => void;
	onError?: (message: string) => void;
}

export interface TrackOptions<T = JobSnapshot> {
	/** Durable event stream to subscribe to (from the enqueue response). */
	eventsUrl?: string;
	/** Snapshot poll cadence; default 1.5s like the letterbox batch. */
	pollMs?: number;
	/** Snapshot fetcher to use instead of the generic `/jobs/{id}`. Pass this
	 *  for job systems with a specialized presentation adapter (e.g. `getMediaJob`)
	 *  so every progress bar can share this one
	 *  poll-plus-SSE engine instead of hand-rolling its own. */
	fetchJob?: (fetchFn: Fetch, jobId: string) => Promise<T>;
}

/** Apply ±20% jitter to a poll interval so many concurrent pollers (batch
 *  pages, multiple tabs) don't hit the API — and its DB pool — in lockstep. */
export function jitterMs(base: number): number {
	return base * (0.8 + Math.random() * 0.4);
}

/** Start tracking; returns a stop() that clears the poll + closes the stream.
 *  Always call it on teardown (component unmount). */
export function trackJob<T extends { status: string; progress?: unknown } = JobSnapshot>(
	fetchFn: Fetch,
	jobId: string,
	handlers: TrackHandlers<T>,
	opts: TrackOptions<T> = {}
): () => void {
	const pollMs = opts.pollMs ?? 1500;
	const fetchJob = (opts.fetchJob ?? getJob) as (fetchFn: Fetch, jobId: string) => Promise<T>;
	let finished = false;
	let timer: ReturnType<typeof setInterval> | null = null;
	let inFlight = false;

	const stop = () => {
		if (timer) {
			clearInterval(timer);
			timer = null;
		}
	};

	const finish = (job: T) => {
		if (finished) return;
		finished = true;
		stop();
		handlers.onDone?.(job);
	};

	const poll = async () => {
		if (finished || inFlight) return;
		inFlight = true;
		try {
			const job = await fetchJob(fetchFn, jobId);
			if (job.progress) {
				handlers.onProgress?.({ status: job.status, detail: job.progress as JobProgressDetail });
			}
			if (isTerminal(job.status)) finish(job);
		} catch {
			/* transient — keep polling */
		} finally {
			inFlight = false;
		}
	};
	void opts.eventsUrl;
	void poll(); // seed immediately so the bar appears
	timer = setInterval(() => void poll(), jitterMs(pollMs));
	return stop;
}
