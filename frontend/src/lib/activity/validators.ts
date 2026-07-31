/**
 * Narrow runtime validators for the Activity client's critical boundaries
 * (JMC6A A03): destructive command responses, job-creation responses, terminal
 * success/outcome snapshots, cursor envelopes, and SSE event envelopes. These are
 * hand-written guards over the generated types — Marquee keeps its existing fetch
 * runtime and does not adopt a second runtime SDK.
 *
 * A malformed or unknown-version response throws `IncompatibleResponseError` so
 * the UI can surface a stale/incompatible-client condition rather than treating
 * bad data as success (A02/§4). Validators never coerce values, so the exact
 * distinction among omitted, `null`, `0`, `false`, and `[]` is preserved.
 */
import { ApiError } from '../api/client';
import type {
	CommandResponse,
	ContainedWorkPage,
	JobEventFrame,
	JobSnapshotResponse,
	JobSubmissionResponse,
	WorkItemPage
} from './types';

/** The SSE frame schema version this client understands (`JobEventFrame.version`). */
export const SUPPORTED_EVENT_FRAME_VERSION = 1;

export class IncompatibleResponseError extends Error {
	constructor(
		public readonly what: string,
		public readonly received: unknown
	) {
		super(`Incompatible ${what} response from server`);
		this.name = 'IncompatibleResponseError';
	}
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isString(value: unknown): value is string {
	return typeof value === 'string';
}

function isFiniteNumber(value: unknown): value is number {
	return typeof value === 'number' && Number.isFinite(value);
}

function isStringOrNull(value: unknown): value is string | null {
	return value === null || typeof value === 'string';
}

type ListEnvelope = { items: unknown[]; limit: number; next_cursor: string | null };

/** Validate a bounded cursor list envelope (`{ items, limit, next_cursor }`). */
export function parseListEnvelope<E extends ListEnvelope>(data: unknown, what: string): E {
	if (
		!isRecord(data) ||
		!Array.isArray(data.items) ||
		!isFiniteNumber(data.limit) ||
		!isStringOrNull(data.next_cursor)
	) {
		throw new IncompatibleResponseError(what, data);
	}
	return data as E;
}

/** Validate a terminal/active job snapshot before it drives UI state. */
export function parseSnapshot(data: unknown): JobSnapshotResponse {
	if (
		!isRecord(data) ||
		data.version !== 1 ||
		!isString(data.job_id) ||
		!isString(data.type) ||
		!isString(data.phase) ||
		!isFiniteNumber(data.fence_token) ||
		!isFiniteNumber(data.progress_sequence) ||
		!('outcome' in data) ||
		!('desired_state' in data) ||
		!('progress' in data)
	) {
		throw new IncompatibleResponseError('job snapshot', data);
	}
	return data as JobSnapshotResponse;
}

/** Validate the stable ordinal work-item page before it drives a scroll region. */
export function parseWorkItemPage(data: unknown): WorkItemPage {
	if (
		!isRecord(data) ||
		data.version !== 1 ||
		!isString(data.job_id) ||
		!Array.isArray(data.items) ||
		!isFiniteNumber(data.limit) ||
		!(data.next_cursor == null || isFiniteNumber(data.next_cursor)) ||
		!isRecord(data.summary) ||
		data.summary.version !== 1 ||
		!isFiniteNumber(data.summary.total) ||
		!isFiniteNumber(data.summary.sequence)
	) {
		throw new IncompatibleResponseError('poster work-item page', data);
	}
	return data as WorkItemPage;
}

/** Validate a source-neutral contained-work page before it drives Activity UI. */
export function parseContainedWorkPage(data: unknown): ContainedWorkPage {
	if (
		!isRecord(data) ||
		data.version !== 1 ||
		!isString(data.job_id) ||
		!Array.isArray(data.items) ||
		!isFiniteNumber(data.limit) ||
		!isStringOrNull(data.next_cursor) ||
		!isRecord(data.summary) ||
		data.summary.version !== 1 ||
		!isString(data.summary.source) ||
		!isFiniteNumber(data.summary.total) ||
		!isFiniteNumber(data.summary.sequence)
	) {
		throw new IncompatibleResponseError('contained-work page', data);
	}
	return data as ContainedWorkPage;
}

/** Validate a destructive/lifecycle command response before applying its effect. */
export function parseCommandResponse(data: unknown): CommandResponse {
	if (!isRecord(data) || !isString(data.action) || !isString(data.execution_class)) {
		throw new IncompatibleResponseError('command', data);
	}
	// The embedded snapshot is the authoritative post-command state.
	parseSnapshot(data.snapshot);
	return data as CommandResponse;
}

/** Validate a job-creation (submission) response so a page can bind to real work. */
export function parseJobSubmission(data: unknown): JobSubmissionResponse {
	if (
		!isRecord(data) ||
		!isString(data.job_id) ||
		!isString(data.phase) ||
		!isString(data.disposition) ||
		!isString(data.snapshot_url) ||
		!isString(data.detail_url)
	) {
		throw new IncompatibleResponseError('job submission', data);
	}
	return data as JobSubmissionResponse;
}

/** Validate one multiplexed SSE frame, including the schema-version guard. */
export function parseJobEventFrame(data: unknown): JobEventFrame {
	if (!isRecord(data)) {
		throw new IncompatibleResponseError('job event frame', data);
	}
	if (data.version !== SUPPORTED_EVENT_FRAME_VERSION) {
		// A newer frame schema means this client is stale/incompatible.
		throw new IncompatibleResponseError('job event frame version', data);
	}
	if (
		!isFiniteNumber(data.cursor) ||
		!isFiniteNumber(data.canonical_version) ||
		!isString(data.event_key) ||
		!isStringOrNull(data.job_id) ||
		!('reconciliation' in data) ||
		!isRecord(data.delta) ||
		!isString(data.delta.state)
	) {
		throw new IncompatibleResponseError('job event frame', data);
	}
	return data as JobEventFrame;
}

/**
 * Extract the structured `JobApiErrorDetail.code` from a failed request so
 * callers can distinguish a stale-command conflict (409) from other failures.
 * Returns `null` when the error is not a structured API error.
 */
export function jobApiErrorCode(error: unknown): string | null {
	if (!(error instanceof ApiError) || !isRecord(error.body)) {
		return null;
	}
	const detail = error.body.detail;
	if (isRecord(detail) && isString(detail.code)) {
		return detail.code;
	}
	return isString(error.body.code) ? error.body.code : null;
}
