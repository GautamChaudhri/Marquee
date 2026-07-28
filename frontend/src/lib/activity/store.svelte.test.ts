import { beforeEach, describe, expect, it } from 'vitest';
import type { EventSourceLike } from './client';
import { JobProgressStore, type JobProgressStoreDeps, type StoreCadence } from './store.svelte';

// ---- deterministic harness -------------------------------------------------

class FakeClock {
	time = 0;
	#seq = 0;
	#tasks = new Map<number, { at: number; cb: () => void }>();
	now = (): number => this.time;
	schedule = (cb: () => void, ms: number): (() => void) => {
		const id = ++this.#seq;
		this.#tasks.set(id, { at: this.time + ms, cb });
		return () => this.#tasks.delete(id);
	};
	advance(ms: number): void {
		const target = this.time + ms;
		for (;;) {
			const due = [...this.#tasks.entries()]
				.filter(([, t]) => t.at <= target)
				.sort((a, b) => a[1].at - b[1].at);
			if (due.length === 0) break;
			const [id, task] = due[0];
			this.#tasks.delete(id);
			this.time = task.at;
			task.cb();
		}
		this.time = target;
	}
}

class FakeEventSource implements EventSourceLike {
	closed = false;
	#listeners = new Map<string, Array<(e: { data?: string }) => void>>();
	constructor(public url: string) {}
	addEventListener(type: string, listener: (e: { data?: string }) => void): void {
		const list = this.#listeners.get(type) ?? [];
		list.push(listener);
		this.#listeners.set(type, list);
	}
	close(): void {
		this.closed = true;
	}
	emit(type: string, data?: unknown): void {
		const payload =
			data === undefined ? {} : { data: typeof data === 'string' ? data : JSON.stringify(data) };
		for (const listener of this.#listeners.get(type) ?? []) listener(payload);
	}
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

function row(id: string, phase = 'running', sequence = 1) {
	return {
		version: 1,
		job_id: id,
		job_type: 'poster_pipeline',
		fence_token: 1,
		label: 'Select a poster',
		label_key: 'jobs.poster_pipeline',
		feature_area: 'posters',
		presentation_family: 'poster',
		subject: { kind: 'movie', display_id: '10', display_name: 'Dune' },
		action_headline: 'Select a poster',
		status: { phase, outcome: phase === 'terminal' ? 'succeeded' : null },
		attention: { level: 'normal' },
		trigger: { kind: 'manual' },
		progress: { sequence, overall: { mode: 'determinate', scope_id: 's', percent: 20 } },
		priority: 0,
		allowed_actions: ['cancel'],
		links: { snapshot: `/api/jobs/${id}/snapshot` }
	};
}

function snapshot(id: string, opts: Record<string, unknown> = {}) {
	return {
		version: 1,
		job_id: id,
		type: 'poster_pipeline',
		label: 'Select a poster',
		phase: 'running',
		outcome: null,
		desired_state: 'run',
		fence_token: 1,
		progress_sequence: 2,
		progress: { sequence: 2, overall: { mode: 'determinate', scope_id: 's', percent: 40 } },
		attention: { level: 'normal' },
		priority: 0,
		allowed_actions: ['cancel'],
		links: { snapshot: `/api/jobs/${id}/snapshot` },
		configuration_version: 1,
		eligible_at: null,
		created_at: null,
		started_at: null,
		terminal_at: null,
		parent_id: null,
		root_id: id,
		retry_of_job_id: null,
		updated_at: null,
		last_event_id: null,
		...opts
	};
}

function progressFrame(id: string, cursor: number, sequence: number) {
	return JSON.stringify({
		version: 1,
		cursor,
		canonical_version: 1,
		event_key: 'progress.updated',
		job_id: id,
		reconciliation: { snapshot_url: `/api/jobs/${id}/snapshot`, presentation_url: '' },
		delta: { state: 'running', detail: { progress_sequence: sequence } }
	});
}

/** A lifecycle frame whose `event_key` matches its SSE event name (as in production). */
function lifecycleFrame(id: string, cursor: number, key: string) {
	return JSON.stringify({
		version: 1,
		cursor,
		canonical_version: 1,
		event_key: key,
		job_id: id,
		reconciliation: { snapshot_url: `/api/jobs/${id}/snapshot`, presentation_url: '' },
		delta: { state: key }
	});
}

interface Harness {
	store: JobProgressStore;
	clock: FakeClock;
	sources: FakeEventSource[];
	calls: Array<{ url: string; signal?: AbortSignal }>;
	setList(body: unknown): void;
	setSnapshot(body: unknown): void;
	failNext(error?: unknown): void;
	setSnapshotStatus(status: number): void;
	setNeverResolve(value: boolean): void;
	hidden: { value: boolean };
	setHidden(value: boolean): void;
}

function setup(cadence: Partial<StoreCadence> = {}): Harness {
	const clock = new FakeClock();
	const sources: FakeEventSource[] = [];
	const calls: Array<{ url: string; signal?: AbortSignal }> = [];
	const hidden = { value: false };
	let listBody: unknown = { items: [], limit: 50, next_cursor: null, view: 'queue' };
	let snapshotBody: unknown = snapshot('j1');
	let pendingError: unknown = null;
	let snapshotStatus = 200;
	let neverResolve = false;
	let visibilityCallback: (() => void) | null = null;

	const fetchImpl = async (url: unknown, init?: RequestInit): Promise<Response> => {
		calls.push({ url: String(url), signal: init?.signal ?? undefined });
		if (neverResolve) {
			return new Promise<Response>((_resolve, reject) => {
				init?.signal?.addEventListener('abort', () =>
					reject(new DOMException('Aborted', 'AbortError'))
				);
			});
		}
		if (pendingError !== null) {
			const err = pendingError;
			pendingError = null;
			throw err;
		}
		const isSnapshot = String(url).includes('/snapshot');
		const body = isSnapshot ? snapshotBody : listBody;
		return new Response(JSON.stringify(body), {
			status: isSnapshot ? snapshotStatus : 200,
			headers: { 'content-type': 'application/json' }
		});
	};

	const deps: JobProgressStoreDeps = {
		fetch: fetchImpl as unknown as JobProgressStoreDeps['fetch'],
		createEventSource: (url: string) => {
			const source = new FakeEventSource(url);
			sources.push(source);
			return source;
		},
		now: clock.now,
		schedule: clock.schedule,
		isHidden: () => hidden.value,
		onVisibilityChange: (callback) => {
			visibilityCallback = callback;
			return () => {
				if (visibilityCallback === callback) visibilityCallback = null;
			};
		},
		random: () => 0.5
	};

	return {
		store: new JobProgressStore(deps, cadence),
		clock,
		sources,
		calls,
		setList: (body) => (listBody = body),
		setSnapshot: (body) => (snapshotBody = body),
		failNext: (error: unknown = new TypeError('network')) => (pendingError = error),
		setSnapshotStatus: (status: number) => (snapshotStatus = status),
		setNeverResolve: (value: boolean) => (neverResolve = value),
		hidden,
		setHidden: (value: boolean) => {
			hidden.value = value;
			visibilityCallback?.();
		}
	};
}

// ---- tests -----------------------------------------------------------------

describe('discovery and single stream', () => {
	let h: Harness;
	beforeEach(() => (h = setup()));

	it('discovers active jobs on first scope use and goes live', async () => {
		h.setList({ items: [row('j1')], limit: 50, next_cursor: null, view: 'queue' });
		h.store.acquireScope('posters', { view: 'queue' });
		await flush();
		expect(h.store.records.get('j1')?.partition).toBe('queue');
		expect(h.store.records.get('j1')?.progress?.overall?.percent).toBe(20);
		expect(h.store.lastSuccessAt).not.toBeNull();
		expect(h.store.activityForScope('posters')).toEqual({
			active: true,
			conflicting: true,
			activeJobIds: ['j1']
		});
	});

	it('derives inactive action authority from an authoritative terminal row', async () => {
		h.setList({ items: [row('j1', 'terminal')], limit: 50, next_cursor: null, view: 'queue' });
		h.store.acquireScope('posters', { view: 'queue' });
		await flush();
		expect(h.store.activityForScope('posters')).toEqual({
			active: false,
			conflicting: false,
			activeJobIds: []
		});
	});

	it('opens exactly one EventSource across many scopes', async () => {
		h.store.acquireScope('a', { view: 'queue' });
		h.store.acquireScope('b', { view: 'queue', feature_area: 'ai_posters' });
		await flush();
		expect(h.sources).toHaveLength(1);
	});

	it('sends exact subject and job-type scope to server discovery', async () => {
		h.store.acquireScope('movie:42', {
			view: 'queue',
			feature_area: 'ai_posters',
			types: ['poster_pipeline', 'poster_restore'],
			subject_kind: 'movie',
			subject_reference: ['42']
		});
		await flush();
		expect(h.calls[0]?.url).toContain('types=poster_pipeline%2Cposter_restore');
		expect(h.calls[0]?.url).toContain('subject_kind=movie');
		expect(h.calls[0]?.url).toContain('subject_reference=42');
	});

	it('lets two independent tabs recover the same canonical active job', async () => {
		const second = setup();
		const response = { items: [row('j1')], limit: 20, next_cursor: null, view: 'queue' };
		h.setList(response);
		second.setList(response);
		h.store.acquireScope('movie:42', { view: 'queue', subject_reference: ['42'] });
		second.store.acquireScope('movie:42', { view: 'queue', subject_reference: ['42'] });
		await flush();
		expect(h.store.activityForScope('movie:42').activeJobIds).toEqual(['j1']);
		expect(second.store.activityForScope('movie:42').activeJobIds).toEqual(['j1']);
		expect(h.sources).toHaveLength(1);
		expect(second.sources).toHaveLength(1);
	});

	it('tears down the stream only after the last consumer releases', async () => {
		const a = h.store.acquireScope('a', { view: 'queue' });
		const b = h.store.acquireScope('a', { view: 'queue' });
		await flush();
		a.release();
		expect(h.sources[0].closed).toBe(false);
		b.release();
		expect(h.sources[0].closed).toBe(true);
	});

	it('aborts an in-flight discovery when its scope is released (navigation)', async () => {
		h.setNeverResolve(true);
		const handle = h.store.acquireScope('posters', { view: 'queue' });
		await flush();
		expect(h.calls[0]?.signal?.aborted).toBe(false);
		handle.release();
		expect(h.calls[0]?.signal?.aborted).toBe(true);
	});

	it('reduces repair cadence while the tab is hidden', async () => {
		h.hidden.value = true;
		h.setList({ items: [row('j1')], limit: 50, next_cursor: null, view: 'queue' });
		h.store.acquireScope('posters', { view: 'queue' });
		await flush();
		const before = h.calls.length;
		h.clock.advance(15_000); // the visible-tab interval must NOT fire a repair
		await flush();
		expect(h.calls.length).toBe(before);
		h.clock.advance(45_000); // hidden interval (60s total) fires the repair loop
		h.clock.advance(400);
		await flush();
		expect(h.calls.length).toBeGreaterThan(before);
	});

	it('rejects reuse of one scope key for a different server filter', async () => {
		h.store.acquireScope('posters:movie:10', { view: 'queue', subject_id: '10' });
		await flush();
		expect(() =>
			h.store.acquireScope('posters:movie:10', { view: 'queue', subject_id: '11' })
		).toThrow('different query');
	});

	it('does not treat absence from a bounded Queue page as terminal removal', async () => {
		h.setList({ items: [row('j1')], limit: 1, next_cursor: 'next', view: 'queue' });
		h.store.acquireScope('posters', { view: 'queue', limit: 1 });
		await flush();
		h.setList({ items: [], limit: 1, next_cursor: null, view: 'queue' });
		h.sources[0].emit('stream.reset_required', {
			version: 1,
			cursor: 80,
			canonical_version: 0,
			event_key: 'stream.reset_required',
			job_id: null,
			reconciliation: null,
			delta: { state: 'reconcile' }
		});
		await flush();
		expect(h.store.records.get('j1')?.partition).toBe('queue');
	});

	it('appends the next bounded cursor page in server order', async () => {
		h.setList({ items: [row('j1')], limit: 1, next_cursor: 'page-2', view: 'queue' });
		h.store.acquireScope('activity', { view: 'queue', limit: 1 });
		await flush();
		h.setList({ items: [row('j2')], limit: 1, next_cursor: null, view: 'queue' });

		h.store.loadMore('activity');
		await flush();

		expect(h.store.recordsForScope('activity').map((record) => record.jobId)).toEqual(['j1', 'j2']);
		expect(h.calls.at(-1)?.url).toContain('cursor=page-2');
	});

	it('preserves last-good scope rows and cursor after a refresh failure', async () => {
		h.setList({ items: [row('j1')], limit: 1, next_cursor: 'page-2', view: 'queue' });
		h.store.acquireScope('activity', { view: 'queue', limit: 1 });
		await flush();
		h.failNext();

		h.store.refreshScope('activity');
		await flush();

		expect(h.store.recordsForScope('activity').map((record) => record.jobId)).toEqual(['j1']);
		expect(h.store.scopeViews.get('activity')).toMatchObject({
			error: 'Activity could not be refreshed. Showing the last good results.',
			nextCursor: 'page-2'
		});
	});

	it('repairs immediately when a hidden tab becomes visible', async () => {
		h.setList({ items: [row('j1')], limit: 50, next_cursor: null, view: 'queue' });
		h.hidden.value = true;
		h.store.acquireScope('posters', { view: 'queue' });
		await flush();
		const before = h.calls.length;
		h.setHidden(false);
		h.clock.advance(400);
		await flush();
		expect(h.calls.length).toBeGreaterThan(before);
	});
});

describe('event application and reconciliation', () => {
	let h: Harness;
	beforeEach(() => (h = setup()));

	it('applies an authoritative snapshot after a progress frame', async () => {
		h.setList({ items: [row('j1', 'running', 1)], limit: 50, next_cursor: null, view: 'queue' });
		h.store.acquireScope('posters', { view: 'queue' });
		await flush();
		h.sources[0].emit('progress.updated', progressFrame('j1', 5, 2));
		h.clock.advance(400); // debounce
		await flush();
		expect(h.store.records.get('j1')?.progress?.overall?.percent).toBe(40);
		expect(h.store.records.get('j1')?.progressSequence).toBe(2);
		expect(h.store.eventCursor).toBe(5);
	});

	it('rejects a duplicate/late progress sequence', async () => {
		h.setList({ items: [row('j1', 'running', 5)], limit: 50, next_cursor: null, view: 'queue' });
		h.setSnapshot(snapshot('j1', { progress_sequence: 5, fence_token: 1 }));
		h.store.acquireScope('posters', { view: 'queue' });
		await flush();
		const before = h.calls.length;
		h.sources[0].emit('progress.updated', progressFrame('j1', 6, 3)); // 3 <= 5
		h.clock.advance(400);
		await flush();
		expect(h.calls.length).toBe(before); // no repair fetch was made
	});

	it('reconciles by rediscovering on a retention reset frame', async () => {
		h.setList({ items: [row('j1')], limit: 50, next_cursor: null, view: 'queue' });
		h.store.acquireScope('posters', { view: 'queue' });
		await flush();
		const before = h.calls.filter((c) => c.url.endsWith('/api/jobs?view=queue')).length;
		h.sources[0].emit('stream.reset_required', {
			version: 1,
			cursor: 99,
			canonical_version: 0,
			event_key: 'stream.reset_required',
			job_id: null,
			reconciliation: null,
			delta: { state: 'reconcile', detail: { high_water: 99 } }
		});
		await flush();
		const after = h.calls.filter((c) => c.url.endsWith('/api/jobs?view=queue')).length;
		expect(after).toBeGreaterThan(before);
		expect(h.store.eventCursor).toBe(99);
	});

	it('becomes incompatible on an unknown event frame version', async () => {
		h.store.acquireScope('posters', { view: 'queue' });
		await flush();
		h.sources[0].emit('progress.updated', JSON.stringify({ version: 2, cursor: 1 }));
		expect(h.store.connection).toBe('incompatible');
	});

	it('becomes incompatible on malformed event JSON', async () => {
		h.store.acquireScope('posters', { view: 'queue' });
		await flush();
		h.sources[0].emit('progress.updated', '{not-json');
		expect(h.store.connection).toBe('incompatible');
	});

	it('rejects a duplicate global cursor before applying a lifecycle hint', async () => {
		h.setList({ items: [row('j1')], limit: 50, next_cursor: null, view: 'queue' });
		h.store.acquireScope('posters', { view: 'queue' });
		await flush();
		h.sources[0].emit('job.failed', lifecycleFrame('j1', 8, 'job.failed'));
		h.clock.advance(400);
		await flush();
		const afterFirst = h.calls.length;
		h.sources[0].emit('job.failed', lifecycleFrame('j1', 8, 'job.failed'));
		h.clock.advance(400);
		await flush();
		expect(h.calls.length).toBe(afterFirst);
	});

	it('coalesces overlapping event repairs to one snapshot request', async () => {
		h.setList({ items: [row('j1')], limit: 50, next_cursor: null, view: 'queue' });
		h.store.acquireScope('posters', { view: 'queue' });
		await flush();
		const before = h.calls.length;
		h.sources[0].emit('progress.updated', progressFrame('j1', 10, 2));
		h.sources[0].emit('progress.updated', progressFrame('j1', 11, 3));
		h.clock.advance(400);
		await flush();
		expect(h.calls.length - before).toBe(1);
	});

	it('ignores an unrelated global event without allocating a record or repair', async () => {
		h.setList({ items: [row('j1')], limit: 50, next_cursor: null, view: 'queue' });
		h.store.acquireScope('posters', { view: 'queue' });
		await flush();
		const before = h.calls.length;
		h.sources[0].emit('job.failed', lifecycleFrame('unrelated-job', 12, 'job.failed'));
		h.clock.advance(400);
		await flush();
		expect(h.calls.length).toBe(before);
		expect(h.store.records.has('unrelated-job')).toBe(false);
	});

	it('inserts a previously unknown explicitly tracked job from its authoritative snapshot', async () => {
		h.setSnapshot(snapshot('snapshot-only'));
		h.store.track('snapshot-only');
		h.clock.advance(400);
		await flush();
		expect(h.store.records.get('snapshot-only')).toMatchObject({
			jobId: 'snapshot-only',
			fenceToken: 1,
			progressSequence: 2,
			partition: 'queue'
		});
	});
});

describe('terminal, fence, and progress preservation', () => {
	let h: Harness;
	beforeEach(() => (h = setup()));

	it('moves a job to History only on an authoritative terminal snapshot', async () => {
		h.setList({ items: [row('j1', 'running', 1)], limit: 50, next_cursor: null, view: 'queue' });
		h.store.acquireScope('posters', { view: 'queue' });
		await flush();
		expect(h.store.records.get('j1')?.partition).toBe('queue');
		h.setSnapshot(
			snapshot('j1', {
				phase: 'terminal',
				outcome: 'failed',
				progress_sequence: 3,
				progress: { sequence: 3, overall: { mode: 'determinate', scope_id: 's', percent: 55 } }
			})
		);
		h.sources[0].emit('job.failed', lifecycleFrame('j1', 7, 'job.failed'));
		h.clock.advance(400);
		await flush();
		const record = h.store.records.get('j1');
		expect(record?.partition).toBe('history');
		expect(record?.freshness).toBe('terminal');
		// last measured progress is preserved, not forced to 100%
		expect(record?.progress?.overall?.percent).toBe(55);
	});

	it('prunes expired terminal Queue cards while retaining the bounded visible tail', async () => {
		const bounded = setup({ terminalRetentionMs: 1, terminalRecordLimit: 1 });
		bounded.setList({
			items: [row('j1', 'running', 1)],
			limit: 50,
			next_cursor: null,
			view: 'queue'
		});
		bounded.store.acquireScope('posters', { view: 'queue' });
		await flush();
		bounded.setSnapshot(snapshot('j1', { phase: 'terminal', outcome: 'succeeded' }));
		bounded.sources[0].emit('job.succeeded', lifecycleFrame('j1', 6, 'job.succeeded'));
		bounded.clock.advance(400);
		await flush();
		expect(bounded.store.records.has('j1')).toBe(true);

		bounded.clock.advance(2);
		bounded.setSnapshot(snapshot('j2', { phase: 'terminal', outcome: 'succeeded' }));
		bounded.store.track('j2');
		bounded.clock.advance(400);
		await flush();
		expect(bounded.store.records.has('j1')).toBe(false);
		expect(bounded.store.records.has('j2')).toBe(true);
	});

	it('accepts a newer attempt (higher fence) and resets its progress scope', async () => {
		h.setList({ items: [row('j1', 'running', 1)], limit: 50, next_cursor: null, view: 'queue' });
		h.setSnapshot(snapshot('j1', { fence_token: 1, progress_sequence: 5 }));
		h.store.acquireScope('posters', { view: 'queue' });
		await flush();
		h.sources[0].emit('progress.updated', progressFrame('j1', 5, 6));
		h.clock.advance(400);
		await flush();
		expect(h.store.records.get('j1')?.fenceToken).toBe(1);
		// redelivered attempt: higher fence, lower sequence — must be accepted, not rejected
		h.setSnapshot(
			snapshot('j1', {
				fence_token: 2,
				progress_sequence: 1,
				progress: { sequence: 1, overall: { mode: 'determinate', scope_id: 't', percent: 5 } }
			})
		);
		h.sources[0].emit('attempt.started', lifecycleFrame('j1', 9, 'attempt.started'));
		h.clock.advance(400);
		await flush();
		const record = h.store.records.get('j1');
		expect(record?.fenceToken).toBe(2);
		expect(record?.progress?.overall?.percent).toBe(5);
	});

	it('repairs a lower progress sequence when the event advances the attempt fence', async () => {
		h.setList({ items: [row('j1', 'running', 6)], limit: 50, next_cursor: null, view: 'queue' });
		h.setSnapshot(
			snapshot('j1', {
				fence_token: 2,
				progress_sequence: 1,
				progress: { sequence: 1, overall: { mode: 'determinate', scope_id: 'retry', percent: 5 } }
			})
		);
		h.store.acquireScope('posters', { view: 'queue' });
		await flush();
		const before = h.calls.length;
		const retried = JSON.parse(progressFrame('j1', 8, 1));
		retried.canonical_version = 2;
		h.sources[0].emit('progress.updated', JSON.stringify(retried));
		h.clock.advance(400);
		await flush();
		expect(h.calls.length).toBe(before + 1);
		expect(h.store.records.get('j1')).toMatchObject({ fenceToken: 2, progressSequence: 1 });
	});

	it('rejects a lower-fence snapshot from the prior attempt', async () => {
		h.setList({ items: [row('j1')], limit: 50, next_cursor: null, view: 'queue' });
		h.setSnapshot(snapshot('j1', { fence_token: 2, progress_sequence: 4 }));
		h.store.acquireScope('posters', { view: 'queue' });
		await flush();
		h.sources[0].emit('attempt.started', lifecycleFrame('j1', 1, 'attempt.started'));
		h.clock.advance(400);
		await flush();
		expect(h.store.records.get('j1')?.fenceToken).toBe(2);
		h.setSnapshot(
			snapshot('j1', {
				fence_token: 1,
				progress_sequence: 99,
				phase: 'terminal',
				outcome: 'failed'
			})
		);
		h.sources[0].emit('job.failed', lifecycleFrame('j1', 2, 'job.failed'));
		h.clock.advance(400);
		await flush();
		expect(h.store.records.get('j1')?.fenceToken).toBe(2);
		expect(h.store.records.get('j1')?.partition).toBe('queue');
	});

	it('ignores a late lifecycle event from a lower canonical fence', async () => {
		h.setList({ items: [row('j1')], limit: 50, next_cursor: null, view: 'queue' });
		h.setSnapshot(snapshot('j1', { fence_token: 3, progress_sequence: 4 }));
		h.store.acquireScope('posters', { view: 'queue' });
		await flush();
		h.sources[0].emit('attempt.started', lifecycleFrame('j1', 1, 'attempt.started'));
		h.clock.advance(400);
		await flush();
		const before = h.calls.length;
		const late = JSON.parse(lifecycleFrame('j1', 2, 'job.failed'));
		late.canonical_version = 2;
		h.sources[0].emit('job.failed', JSON.stringify(late));
		h.clock.advance(400);
		await flush();
		expect(h.calls.length).toBe(before);
	});
});

describe('connection resilience', () => {
	let h: Harness;
	beforeEach(() => (h = setup()));

	it('marks reconnecting on a stream error without dropping records', async () => {
		h.setList({ items: [row('j1')], limit: 50, next_cursor: null, view: 'queue' });
		h.store.acquireScope('posters', { view: 'queue' });
		await flush();
		h.sources[0].emit('error');
		expect(h.store.connection).toBe('reconnecting');
		expect(h.store.records.has('j1')).toBe(true);
		h.sources[0].emit('open');
		expect(h.store.connection).toBe('live');
	});

	it('escalates to stale after repeated stream errors', async () => {
		h.store.acquireScope('posters', { view: 'queue' });
		await flush();
		h.sources[0].emit('error');
		h.sources[0].emit('error');
		h.sources[0].emit('error');
		expect(h.store.connection).toBe('stale');
	});

	it('preserves last-good data and retries with backoff after an API failure', async () => {
		h.setList({ items: [row('j1')], limit: 50, next_cursor: null, view: 'queue' });
		h.store.acquireScope('posters', { view: 'queue' });
		await flush();
		expect(h.store.records.has('j1')).toBe(true);
		h.failNext();
		h.clock.advance(15_000); // repair loop tick triggers a repair that fails
		h.clock.advance(400);
		await flush();
		// the job card survives a failed request
		expect(h.store.records.has('j1')).toBe(true);
	});

	it('recovers after a simulated API or PostgreSQL restart', async () => {
		h.setList({ items: [row('j1')], limit: 50, next_cursor: null, view: 'queue' });
		h.store.acquireScope('posters', { view: 'queue' });
		await flush();
		h.failNext(new TypeError('connection reset'));
		h.sources[0].emit('progress.updated', progressFrame('j1', 20, 2));
		h.clock.advance(400);
		await flush();
		expect(h.store.connection).toBe('reconnecting');
		expect(h.store.records.has('j1')).toBe(true);
		h.setSnapshot(snapshot('j1', { progress_sequence: 3 }));
		h.clock.advance(1_000);
		await flush();
		expect(h.store.connection).toBe('live');
		expect(h.store.records.get('j1')?.progressSequence).toBe(3);
	});

	it('keeps last-good data but becomes incompatible on a newer snapshot version', async () => {
		h.setList({ items: [row('j1')], limit: 50, next_cursor: null, view: 'queue' });
		h.store.acquireScope('posters', { view: 'queue' });
		await flush();
		h.setSnapshot(snapshot('j1', { version: 2 }));
		h.sources[0].emit('progress.updated', progressFrame('j1', 21, 2));
		h.clock.advance(400);
		await flush();
		expect(h.store.connection).toBe('incompatible');
		expect(h.store.records.has('j1')).toBe(true);
	});

	it('cancels a pending snapshot retry when the final consumer releases', async () => {
		h.setList({ items: [row('j1')], limit: 50, next_cursor: null, view: 'queue' });
		const handle = h.store.acquireScope('posters', { view: 'queue' });
		await flush();
		h.failNext();
		h.sources[0].emit('progress.updated', progressFrame('j1', 30, 2));
		h.clock.advance(400);
		await flush();
		const beforeRelease = h.calls.length;
		handle.release();
		h.clock.advance(30_000);
		await flush();
		expect(h.calls.length).toBe(beforeRelease);
	});

	it('rediscovers from the server with local storage empty', async () => {
		localStorage.clear();
		h.setList({ items: [row('j1')], limit: 50, next_cursor: null, view: 'queue' });
		h.store.acquireScope('posters', { view: 'queue' });
		await flush();
		expect(h.store.records.has('j1')).toBe(true);
	});

	it('reopens one stream from the durable cursor after an explicit stop', async () => {
		h.store.acquireScope('posters', { view: 'queue' });
		await flush();
		h.sources[0].emit('progress.updated', progressFrame('j1', 42, 1));
		h.store.stop();
		expect(h.store.connection).toBe('stopped');
		h.store.acquireScope('posters-again', { view: 'queue' });
		await flush();
		expect(h.sources).toHaveLength(2);
		expect(h.sources[1].url).toBe('/api/jobs/events/stream?after=42');
	});
});

describe('vanished jobs', () => {
	let h: Harness;
	beforeEach(() => (h = setup()));

	it('forgets a job whose snapshot 404s instead of retrying it forever', async () => {
		h.setList({ items: [row('j1')], limit: 50, next_cursor: null, view: 'queue' });
		h.store.acquireScope('posters', { view: 'queue' });
		await flush();
		expect(h.store.records.get('j1')).toBeDefined();

		// A database reset or retention purge removes the job under the poller.
		h.setSnapshotStatus(404);
		h.sources[0].emit('progress.updated', progressFrame('j1', 10, 2));
		h.clock.advance(400);
		await flush();

		expect(h.store.records.get('j1')).toBeUndefined();

		// No backoff timer survives to keep asking for a job that is gone.
		const snapshotCalls = () => h.calls.filter((call) => call.url.includes('/snapshot')).length;
		const settled = snapshotCalls();
		h.clock.advance(120_000);
		await flush();
		expect(snapshotCalls()).toBe(settled);
	});

	it('keeps retrying a transient failure', async () => {
		h.setList({ items: [row('j1')], limit: 50, next_cursor: null, view: 'queue' });
		h.store.acquireScope('posters', { view: 'queue' });
		await flush();

		h.failNext(new TypeError('network'));
		h.sources[0].emit('progress.updated', progressFrame('j1', 10, 2));
		h.clock.advance(400);
		await flush();

		expect(h.store.records.get('j1')).toBeDefined();
	});
});
