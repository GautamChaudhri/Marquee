/**
 * `JobProgressStore` — one browser-session, server-authoritative state machine for
 * active job discovery, reconciliation, and progress (JMC6A A04–A10, §5).
 *
 * It is a testable state machine: every side-effecting dependency (fetch,
 * EventSource, timers, page visibility, clock, jitter) is injected, so the whole
 * reconciliation surface — dropped/duplicate/late/reset events, request overlap,
 * navigation races, API/DB restarts, retry/redelivery, hidden tabs — is driven
 * deterministically from tests without real time or a real backend.
 *
 * Ownership:
 *  - reference-counted Queue/list scopes keyed by feature/subject/filter;
 *  - one compact canonical record (+ optional latest snapshot) per job (A10 —
 *    presentations/logs/artifacts/children stay lazy and are never embedded);
 *  - one multiplexed EventSource for the whole session (A06);
 *  - a global event cursor plus per-job progress sequence/fence (A09);
 *  - connection freshness and low-frequency, abortable, hidden-tab-aware,
 *    backoff-bounded snapshot repair with one in-flight request per scope/job (A08).
 *
 * The server is authoritative (A05): a stream error only changes connection state
 * and preserves the last good data (A07); a job moves to History only on an
 * authoritative terminal snapshot, never on a dropped stream.
 */
import { SvelteMap, SvelteSet } from 'svelte/reactivity';
import type { Fetch } from '../api/client';
import {
	getSnapshot,
	jobEventStreamUrl,
	listJobs,
	type EventSourceFactory,
	type EventSourceLike
} from './client';
import { IncompatibleResponseError, parseJobEventFrame } from './validators';
import type { CompactProgress, JobRow, JobSnapshotResponse, ListJobsQuery } from './types';

export type ConnectionState =
	'initial' | 'loading' | 'live' | 'reconnecting' | 'stale' | 'incompatible' | 'stopped';

export type RecordFreshness = 'live' | 'stale' | 'terminal';
export type Partition = 'queue' | 'history';

export interface JobRecord {
	readonly jobId: string;
	readonly row: JobRow | null;
	readonly snapshot: JobSnapshotResponse | null;
	readonly progress: CompactProgress | null;
	readonly progressSequence: number;
	readonly fenceToken: number | null;
	readonly partition: Partition;
	readonly freshness: RecordFreshness;
	readonly updatedAt: number;
}

/** A cancel function returned by the injected scheduler. */
export type Cancel = () => void;

export interface JobProgressStoreDeps {
	fetch: Fetch;
	createEventSource: EventSourceFactory;
	now: () => number;
	/** Schedule `callback` after `delayMs`; the returned function cancels it. */
	schedule: (callback: () => void, delayMs: number) => Cancel;
	isHidden: () => boolean;
	onVisibilityChange: (callback: () => void) => Cancel;
	random?: () => number;
}

export interface StoreCadence {
	repairMs: number;
	hiddenRepairMs: number;
	debounceMs: number;
	backoffBaseMs: number;
	backoffMaxMs: number;
	staleAfterMs: number;
	/** Keep completed Queue cards briefly, without retaining a session-long terminal union. */
	terminalRetentionMs: number;
	/** Safety bound for terminal cards retained by Queue scopes in one browser session. */
	terminalRecordLimit: number;
}

export const DEFAULT_CADENCE: StoreCadence = {
	repairMs: 15_000,
	hiddenRepairMs: 60_000,
	debounceMs: 400,
	backoffBaseMs: 1_000,
	backoffMaxMs: 30_000,
	staleAfterMs: 30_000,
	terminalRetentionMs: 5 * 60_000,
	terminalRecordLimit: 100
};

const PROGRESS_EVENT = 'progress.updated';
const RESET_EVENT = 'stream.reset_required';

/**
 * Event keys that warrant an authoritative snapshot repair for their job. The set
 * is an optimisation for promptness — the periodic repair loop and Queue
 * rediscovery are the correctness backstop, so an unlisted future key still
 * reconciles, just a little later.
 */
const LIFECYCLE_EVENTS = [
	'job.planned',
	'job.queued',
	'job.stopping',
	'job.paused',
	'job.resumed',
	'job.retry_requested',
	'job.retried',
	'job.priority_changed',
	'job.succeeded',
	'job.failed',
	'job.cancelled',
	'job.no_change',
	'job.partially_succeeded',
	'job.superseded',
	'job.dead_letter',
	'job.unsafe',
	'attempt.started',
	'attempt.interrupted',
	'attention.updated',
	'artifact.available',
	'artifact.expired',
	'artifact.failed',
	'log.available',
	'log.truncated',
	'ml.publication.activated',
	'batch.opened',
	'batch.child_appended',
	'batch.projected',
	'batch.sealed',
	'batch.repaired'
];

const SUBSCRIBED_EVENTS = [PROGRESS_EVENT, RESET_EVENT, ...LIFECYCLE_EVENTS];

interface ScopeState {
	key: string;
	query: ListJobsQuery;
	querySignature: string;
	refs: number;
	jobIds: Set<string>;
	inFlight: AbortController | null;
	backoffMs: number;
	retryCancel: Cancel | null;
}

interface JobRepairState {
	inFlight: AbortController | null;
	debounceCancel: Cancel | null;
	retryCancel: Cancel | null;
	backoffMs: number;
}

export interface ScopeHandle {
	readonly key: string;
	release(): void;
}

export interface ScopeView {
	readonly key: string;
	readonly jobIds: readonly string[];
	readonly loading: boolean;
	readonly error: string | null;
	readonly nextCursor: string | null;
}

export interface ScopeActivityState {
	readonly active: boolean;
	/** Matching active work that makes a same-scope unsafe action conflict server-side. */
	readonly conflicting: boolean;
	readonly activeJobIds: readonly string[];
}

export class JobProgressStore {
	connection = $state<ConnectionState>('initial');
	lastSuccessAt = $state<number | null>(null);
	eventCursor = $state<number | null>(null);
	readonly records = new SvelteMap<string, JobRecord>();
	readonly scopeViews = new SvelteMap<string, ScopeView>();

	readonly #deps: JobProgressStoreDeps;
	readonly #cadence: StoreCadence;
	readonly #scopes = new SvelteMap<string, ScopeState>();
	readonly #repairs = new SvelteMap<string, JobRepairState>();
	readonly #trackedIds = new SvelteSet<string>();
	#es: EventSourceLike | null = null;
	#esErrorStreak = 0;
	#visibilityCancel: Cancel | null = null;
	#repairLoopCancel: Cancel | null = null;
	#stopped = false;

	constructor(deps: JobProgressStoreDeps, cadence: Partial<StoreCadence> = {}) {
		this.#deps = deps;
		this.#cadence = { ...DEFAULT_CADENCE, ...cadence };
	}

	// ---- public API --------------------------------------------------------

	/** Acquire (or reference) a keyed Queue scope; discovery runs on first use. */
	acquireScope(key: string, query: ListJobsQuery): ScopeHandle {
		this.#reviveIfStopped();
		const existing = this.#scopes.get(key);
		if (existing) {
			if (existing.querySignature !== querySignature(query)) {
				throw new Error(`JobProgressStore scope "${key}" was acquired with a different query`);
			}
			existing.refs += 1;
		} else {
			const scope: ScopeState = {
				key,
				query,
				querySignature: querySignature(query),
				refs: 1,
				jobIds: new SvelteSet(),
				inFlight: null,
				backoffMs: this.#cadence.backoffBaseMs,
				retryCancel: null
			};
			this.#scopes.set(key, scope);
			this.#publishScope(scope, { loading: true });
			this.#ensureStarted();
			void this.#discover(scope);
		}
		return { key, release: () => this.#releaseScope(key) };
	}

	/** Canonical records currently discovered for one list scope, in server order. */
	recordsForScope(key: string): JobRecord[] {
		const view = this.scopeViews.get(key);
		if (!view) return [];
		return view.jobIds
			.map((jobId) => this.records.get(jobId))
			.filter((record): record is JobRecord => record !== undefined);
	}

	/** Server-derived action authority for an exact scope plus newly submitted job ids. */
	activityForScope(key: string, additionalJobIds: readonly string[] = []): ScopeActivityState {
		const ids = new SvelteSet(this.scopeViews.get(key)?.jobIds ?? []);
		for (const jobId of additionalJobIds) ids.add(jobId);
		const activeJobIds = [...ids].filter((jobId) => {
			const record = this.records.get(jobId);
			const phase = record?.snapshot?.phase ?? record?.row?.status.phase;
			return phase !== undefined && phase !== 'terminal';
		});
		return {
			active: activeJobIds.length > 0,
			conflicting: activeJobIds.length > 0,
			activeJobIds
		};
	}

	/** Load the next stable server cursor for a scope, if one exists. */
	loadMore(key: string): void {
		const scope = this.#scopes.get(key);
		const cursor = this.scopeViews.get(key)?.nextCursor;
		if (!scope || !cursor || scope.inFlight) return;
		void this.#discover(scope, cursor);
	}

	/** Re-run first-page discovery while preserving last-good rows on failure. */
	refreshScope(key: string): void {
		const scope = this.#scopes.get(key);
		if (!scope) return;
		scope.inFlight?.abort();
		scope.inFlight = null;
		scope.retryCancel?.();
		scope.retryCancel = null;
		void this.#discover(scope);
	}

	/** Bind directly to a known job id (e.g. a just-submitted job) via its snapshot. */
	track(jobId: string): void {
		this.#reviveIfStopped();
		this.#trackedIds.add(jobId);
		this.#ensureStarted();
		this.#scheduleRepair(jobId);
	}

	/** Stop directly binding `jobId`; the record is dropped if no scope references it. */
	untrack(jobId: string): void {
		this.#trackedIds.delete(jobId);
		this.#pruneUnreferencedRecords();
	}

	/** Stop everything and release all connections/timers, keeping last-good data. */
	stop(): void {
		this.#trackedIds.clear();
		for (const scope of this.#scopes.values()) {
			scope.inFlight?.abort();
			scope.retryCancel?.();
		}
		this.#scopes.clear();
		this.#teardown();
		this.connection = 'stopped';
	}

	// ---- lifecycle ---------------------------------------------------------

	#reviveIfStopped(): void {
		if (this.#stopped) {
			this.#stopped = false;
			this.connection = 'initial';
		}
	}

	#ensureStarted(): void {
		if (this.#es || this.#stopped) return;
		if (this.connection === 'initial') this.connection = 'loading';
		this.#openStream();
		this.#visibilityCancel = this.#deps.onVisibilityChange(() => this.#onVisibilityChange());
		this.#scheduleRepairLoop();
	}

	#releaseScope(key: string): void {
		const scope = this.#scopes.get(key);
		if (!scope) return;
		scope.refs -= 1;
		if (scope.refs > 0) return;
		scope.inFlight?.abort();
		scope.retryCancel?.();
		this.#scopes.delete(key);
		this.scopeViews.delete(key);
		this.#pruneUnreferencedRecords();
		if (this.#scopes.size === 0 && this.#trackedIds.size === 0) this.#teardown();
	}

	#teardown(): void {
		this.#stopped = true;
		this.#es?.close();
		this.#es = null;
		this.#visibilityCancel?.();
		this.#visibilityCancel = null;
		this.#repairLoopCancel?.();
		this.#repairLoopCancel = null;
		for (const scope of this.#scopes.values()) {
			scope.inFlight?.abort();
			scope.retryCancel?.();
		}
		for (const repair of this.#repairs.values()) {
			repair.inFlight?.abort();
			repair.debounceCancel?.();
			repair.retryCancel?.();
		}
		this.#repairs.clear();
	}

	// ---- Queue discovery ---------------------------------------------------

	async #discover(scope: ScopeState, cursor: string | null = null): Promise<void> {
		if (this.#stopped || scope.inFlight) return; // one in-flight per scope
		scope.retryCancel?.();
		scope.retryCancel = null;
		const controller = new AbortController();
		scope.inFlight = controller;
		this.#publishScope(scope, { loading: true, error: null });
		try {
			const response = await listJobs(this.#withSignal(controller), {
				...scope.query,
				cursor: cursor ?? undefined
			});
			if (controller.signal.aborted || this.#stopped) return;
			// A bounded first page cannot prove that a previously discovered job is
			// gone. Keep the union and let its authoritative snapshot move it to
			// History; absence from one Queue page never removes a card.
			for (const row of response.items) scope.jobIds.add(row.job_id);
			for (const row of response.items) this.#mergeRow(row);
			this.#publishScope(scope, {
				loading: false,
				error: null,
				nextCursor: response.next_cursor
			});
			this.#pruneUnreferencedRecords();
			scope.backoffMs = this.#cadence.backoffBaseMs;
			this.#markSuccess();
		} catch (error) {
			if (controller.signal.aborted || this.#stopped) return;
			this.#handleRequestFailure(error);
			this.#publishScope(scope, {
				loading: false,
				error: 'Activity could not be refreshed. Showing the last good results.'
			});
			scope.retryCancel = this.#deps.schedule(() => {
				scope.retryCancel = null;
				void this.#discover(scope);
			}, this.#nextBackoff(scope.backoffMs));
			scope.backoffMs = this.#growBackoff(scope.backoffMs);
		} finally {
			if (scope.inFlight === controller) scope.inFlight = null;
		}
	}

	#publishScope(scope: ScopeState, update: Partial<Omit<ScopeView, 'key' | 'jobIds'>>): void {
		const previous = this.scopeViews.get(scope.key);
		this.scopeViews.set(scope.key, {
			key: scope.key,
			jobIds: [...scope.jobIds],
			loading: update.loading ?? previous?.loading ?? false,
			error: update.error === undefined ? (previous?.error ?? null) : update.error,
			nextCursor:
				update.nextCursor === undefined ? (previous?.nextCursor ?? null) : update.nextCursor
		});
	}

	// ---- event stream ------------------------------------------------------

	#openStream(): void {
		const source = this.#deps.createEventSource(jobEventStreamUrl(this.eventCursor));
		this.#es = source;
		source.addEventListener('open', () => this.#onStreamOpen());
		source.addEventListener('error', () => this.#onStreamError());
		for (const type of SUBSCRIBED_EVENTS) {
			source.addEventListener(type, (event) => this.#onFrame(event));
		}
	}

	#onStreamOpen(): void {
		this.#esErrorStreak = 0;
		if (this.connection !== 'incompatible' && this.connection !== 'stopped') {
			this.connection = 'live';
		}
	}

	#onStreamError(): void {
		if (this.#stopped || this.connection === 'incompatible') return;
		this.#esErrorStreak += 1;
		// Native EventSource reconnects on its own; never fail a job on a stream drop.
		this.connection = this.#esErrorStreak >= 3 ? 'stale' : 'reconnecting';
		this.#refreshFreshness();
	}

	#onFrame(event: { data?: string }): void {
		if (this.#stopped || this.connection === 'incompatible') return;
		if (typeof event.data !== 'string') return;
		let frame: ReturnType<typeof parseJobEventFrame>;
		try {
			frame = parseJobEventFrame(JSON.parse(event.data));
		} catch {
			// Malformed JSON and schema/version mismatches are both incompatible
			// wire data. Never silently treat either as a successful update.
			this.connection = 'incompatible';
			return;
		}
		if (this.eventCursor !== null && frame.cursor <= this.eventCursor) return;
		this.eventCursor = frame.cursor;
		if (this.connection === 'reconnecting' || this.connection === 'stale') {
			this.connection = 'live';
			this.#esErrorStreak = 0;
		}
		if (frame.event_key === RESET_EVENT) {
			void this.#reconcile();
			return;
		}
		if (frame.job_id === null) return;
		if (!this.#isRelevantJob(frame.job_id)) return;
		const record = this.records.get(frame.job_id);
		if (
			record?.fenceToken !== null &&
			record?.fenceToken !== undefined &&
			frame.canonical_version < record.fenceToken
		) {
			return; // durable event from a superseded attempt
		}
		if (frame.event_key === PROGRESS_EVENT) {
			this.#applyProgressFrame(frame.job_id, frame);
			return;
		}
		this.#scheduleRepair(frame.job_id);
	}

	#applyProgressFrame(jobId: string, frame: ReturnType<typeof parseJobEventFrame>): void {
		const record = this.records.get(jobId);
		const sequence = frame.delta.detail?.progress_sequence;
		const currentFence = record?.fenceToken ?? null;
		if (
			record &&
			typeof sequence === 'number' &&
			currentFence !== null &&
			frame.canonical_version <= currentFence &&
			sequence <= record.progressSequence
		) {
			return; // duplicate or late progress — reject (A09)
		}
		// The frame is only a hint; fetch the authoritative CompactProgress.
		this.#scheduleRepair(jobId);
	}

	// ---- snapshot repair ---------------------------------------------------

	#scheduleRepair(jobId: string): void {
		if (this.#stopped || !this.#isRelevantJob(jobId)) return;
		let repair = this.#repairs.get(jobId);
		if (!repair) {
			repair = {
				inFlight: null,
				debounceCancel: null,
				retryCancel: null,
				backoffMs: this.#cadence.backoffBaseMs
			};
			this.#repairs.set(jobId, repair);
		}
		if (repair.debounceCancel || repair.inFlight) return; // coalesce
		repair.debounceCancel = this.#deps.schedule(() => {
			repair.debounceCancel = null;
			void this.#repairJob(jobId);
		}, this.#cadence.debounceMs);
	}

	async #repairJob(jobId: string): Promise<void> {
		const repair = this.#repairs.get(jobId);
		if (!repair || repair.inFlight || this.#stopped) return;
		const controller = new AbortController();
		repair.inFlight = controller;
		try {
			repair.retryCancel?.();
			repair.retryCancel = null;
			const snapshot = await getSnapshot(this.#withSignal(controller), jobId);
			if (controller.signal.aborted || this.#stopped) return;
			this.#applySnapshot(snapshot);
			repair.backoffMs = this.#cadence.backoffBaseMs;
			this.#markSuccess();
		} catch (error) {
			if (controller.signal.aborted || this.#stopped) return;
			this.#handleRequestFailure(error);
			repair.retryCancel = this.#deps.schedule(() => {
				repair.retryCancel = null;
				void this.#repairJob(jobId);
			}, this.#nextBackoff(repair.backoffMs));
			repair.backoffMs = this.#growBackoff(repair.backoffMs);
		} finally {
			if (repair.inFlight === controller) repair.inFlight = null;
		}
	}

	#repairAll(): void {
		for (const [jobId, record] of this.records) {
			if (record.partition === 'history') continue; // terminal jobs are settled
			this.#scheduleRepair(jobId);
		}
	}

	#scheduleRepairLoop(): void {
		this.#repairLoopCancel?.();
		const interval = this.#deps.isHidden() ? this.#cadence.hiddenRepairMs : this.#cadence.repairMs;
		this.#repairLoopCancel = this.#deps.schedule(() => {
			this.#repairLoopCancel = null;
			if (this.#stopped) return;
			this.#refreshFreshness();
			this.#repairAll();
			this.#scheduleRepairLoop();
		}, interval);
	}

	async #reconcile(): Promise<void> {
		// Retention gap: re-discover every scope and repair known jobs from bounded
		// snapshots rather than assuming missed events can be replayed (A09).
		for (const scope of this.#scopes.values()) void this.#discover(scope);
		this.#repairAll();
	}

	// ---- record merges -----------------------------------------------------

	#mergeRow(row: JobRow): void {
		const existing = this.records.get(row.job_id);
		const partition: Partition = row.status.phase === 'terminal' ? 'history' : 'queue';
		this.records.set(row.job_id, {
			jobId: row.job_id,
			row,
			snapshot: existing?.snapshot ?? null,
			progress: existing?.progress ?? row.progress ?? null,
			progressSequence: existing?.progressSequence ?? row.progress?.sequence ?? 0,
			fenceToken: row.fence_token,
			partition,
			freshness: partition === 'history' ? 'terminal' : 'live',
			updatedAt: this.#deps.now()
		});
	}

	#applySnapshot(snapshot: JobSnapshotResponse): void {
		const existing = this.records.get(snapshot.job_id);
		if (!existing && !this.#isRelevantJob(snapshot.job_id)) return;
		const priorFence = existing?.fenceToken ?? null;
		if (priorFence !== null && snapshot.fence_token < priorFence) {
			return; // stale writer from a superseded attempt
		}
		const sameAttempt = priorFence !== null && snapshot.fence_token === priorFence;
		const newerAttempt = priorFence !== null && snapshot.fence_token > priorFence;
		if (existing && sameAttempt && snapshot.progress_sequence < existing.progressSequence) {
			return; // stale/out-of-order snapshot for the current attempt — reject
		}
		const terminal = snapshot.phase === 'terminal';
		// A retry (new fence) starts a fresh progress scope; a late writer from the
		// prior attempt cannot regress or finish this one (§5). Terminal failure/
		// cancellation keeps the last measured progress — never force 100% (A13).
		const progress = newerAttempt
			? snapshot.progress
			: (snapshot.progress ?? existing?.progress ?? null);
		this.records.set(snapshot.job_id, {
			jobId: snapshot.job_id,
			row: existing?.row ?? null,
			snapshot,
			progress,
			progressSequence: snapshot.progress_sequence,
			fenceToken: snapshot.fence_token,
			partition: terminal ? 'history' : 'queue',
			freshness: terminal ? 'terminal' : 'live',
			updatedAt: this.#deps.now()
		});
		this.#pruneTerminalRecords();
	}

	#pruneUnreferencedRecords(): void {
		const referenced = new SvelteSet<string>(this.#trackedIds);
		for (const scope of this.#scopes.values()) for (const id of scope.jobIds) referenced.add(id);
		for (const id of this.records.keys()) {
			if (!referenced.has(id)) {
				this.records.delete(id);
				const repair = this.#repairs.get(id);
				repair?.inFlight?.abort();
				repair?.debounceCancel?.();
				repair?.retryCancel?.();
				this.#repairs.delete(id);
			}
		}
	}

	/**
	 * Queue discovery deliberately keeps a union so a bounded first page cannot
	 * erase a live card. Once a terminal snapshot is authoritative, however,
	 * that union must not become a session-long history cache. History scopes
	 * remain server-bounded and retain their visible rows; Queue scopes keep only
	 * a short, bounded terminal tail for completion feedback.
	 */
	#pruneTerminalRecords(): void {
		const now = this.#deps.now();
		const terminalQueueRecords = [...this.records.values()]
			.filter(
				(record) =>
					record.partition === 'history' &&
					[...this.#scopes.values()].some(
						(scope) => scope.query.view === 'queue' && scope.jobIds.has(record.jobId)
					)
			)
			.sort((left, right) => right.updatedAt - left.updatedAt);
		const pruned = new SvelteSet<string>();
		for (const [index, record] of terminalQueueRecords.entries()) {
			if (
				index < this.#cadence.terminalRecordLimit &&
				now - record.updatedAt <= this.#cadence.terminalRetentionMs
			) {
				continue;
			}
			pruned.add(record.jobId);
		}
		if (pruned.size === 0) return;
		for (const scope of this.#scopes.values()) {
			if (scope.query.view !== 'queue') continue;
			let changed = false;
			for (const jobId of pruned) changed = scope.jobIds.delete(jobId) || changed;
			if (changed) this.#publishScope(scope, {});
		}
		this.#pruneUnreferencedRecords();
	}

	/** A global SSE frame can only allocate work for an already relevant job. */
	#isRelevantJob(jobId: string): boolean {
		if (this.#trackedIds.has(jobId) || this.records.has(jobId)) return true;
		for (const scope of this.#scopes.values()) {
			if (scope.jobIds.has(jobId)) return true;
		}
		return false;
	}

	// ---- connection + freshness helpers ------------------------------------

	#markSuccess(): void {
		this.lastSuccessAt = this.#deps.now();
		if (
			this.connection === 'loading' ||
			this.connection === 'reconnecting' ||
			this.connection === 'stale'
		) {
			this.connection = 'live';
		}
		this.#refreshFreshness();
	}

	#handleRequestFailure(error: unknown): void {
		if (error instanceof IncompatibleResponseError) {
			this.connection = 'incompatible';
			return;
		}
		if (this.connection === 'live' || this.connection === 'loading') {
			this.connection = 'reconnecting';
		}
		this.#refreshFreshness();
	}

	#refreshFreshness(): void {
		const now = this.#deps.now();
		const degraded = this.connection === 'reconnecting' || this.connection === 'stale';
		for (const [id, record] of this.records) {
			if (record.partition === 'history') continue;
			const stale = degraded || now - record.updatedAt > this.#cadence.staleAfterMs;
			const freshness: RecordFreshness = stale ? 'stale' : 'live';
			if (freshness !== record.freshness) this.records.set(id, { ...record, freshness });
		}
		this.#pruneTerminalRecords();
	}

	#onVisibilityChange(): void {
		if (!this.#deps.isHidden()) this.#repairAll();
		this.#scheduleRepairLoop();
	}

	// ---- utilities ---------------------------------------------------------

	#withSignal(controller: AbortController): Fetch {
		return ((input: Parameters<Fetch>[0], init?: RequestInit) => {
			const signal = init?.signal
				? AbortSignal.any([init.signal, controller.signal])
				: controller.signal;
			return this.#deps.fetch(input, { ...init, signal });
		}) as Fetch;
	}

	#nextBackoff(base: number): number {
		const jitter = 0.8 + (this.#deps.random ?? Math.random)() * 0.4;
		return Math.min(base, this.#cadence.backoffMaxMs) * jitter;
	}

	#growBackoff(base: number): number {
		return Math.min(base * 2, this.#cadence.backoffMaxMs);
	}
}

function querySignature(query: ListJobsQuery): string {
	return JSON.stringify(
		Object.entries(query)
			.filter(([, value]) => value !== undefined)
			.sort(([left], [right]) => left.localeCompare(right))
	);
}
