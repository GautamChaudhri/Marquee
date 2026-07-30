import { render, screen, waitFor } from '@testing-library/svelte';
import { SvelteMap } from 'svelte/reactivity';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { JobRecord } from '../store.svelte';
import type { JobRow, JobSnapshotResponse, ListJobsQuery } from '../types';
import FeatureActivityPanel from './FeatureActivityPanel.svelte';
import { makeRow, makeSnapshot, subjects } from './fixtures';

const { getStore } = vi.hoisted(() => ({ getStore: vi.fn() }));

vi.mock('../context', () => ({ getJobProgressStore: getStore }));

function row(jobId: string, label: string, subject: JobRow['subject'], terminal = false): JobRow {
	return makeRow({
		job_id: jobId,
		label,
		subject: { ...subject, display_name: label },
		status: terminal
			? {
					label: 'Complete',
					label_key: 'jobs.status.succeeded',
					phase: 'terminal',
					outcome: 'succeeded',
					tone: 'positive'
				}
			: makeRow().status,
		terminal_at: terminal ? '2026-07-16T12:02:00Z' : null
	});
}

function record(value: JobRow): JobRecord {
	return {
		jobId: value.job_id,
		row: value,
		snapshot: makeSnapshot(value),
		progress: value.progress ?? null,
		progressSequence: value.progress?.sequence ?? 0,
		fenceToken: value.fence_token,
		partition: value.status.phase === 'terminal' ? 'history' : 'queue',
		freshness: value.status.phase === 'terminal' ? 'terminal' : 'live',
		updatedAt: 1
	};
}

function fakeStore(values: JobRecord[]) {
	const records = new SvelteMap(values.map((value) => [value.jobId, value]));
	const scoped = new SvelteMap<string, JobRecord[]>();
	return {
		records,
		connection: 'live',
		acquireScope(key: string, query: ListJobsQuery) {
			const kind = query.type === 'poster_pipeline_tv_batch' ? 'batch' : query.subject_kind;
			scoped.set(
				key,
				values.filter((value) =>
					kind === 'batch'
						? value.row?.job_type === 'poster_pipeline_tv_batch'
						: value.row?.subject.kind === kind
				)
			);
			return { key, release: vi.fn() };
		},
		recordsForScope(key: string) {
			return (scoped.get(key) ?? []).map((value) => records.get(value.jobId) ?? value);
		},
		activityForScope(key: string, additionalJobIds: readonly string[] = []) {
			const ids = new Set([
				...(scoped.get(key) ?? []).map((value) => value.jobId),
				...additionalJobIds
			]);
			const activeJobIds = [...ids].filter(
				(jobId) => records.get(jobId)?.snapshot?.phase !== 'terminal'
			);
			return {
				active: activeJobIds.length > 0,
				conflicting: activeJobIds.length > 0,
				activeJobIds
			};
		},
		track: vi.fn(),
		untrack: vi.fn(),
		refreshScope: vi.fn()
	};
}

describe('FeatureActivityPanel TV batches', () => {
	beforeEach(() => getStore.mockReset());

	it('unions parent, series, and season scopes but settles only the tracked parent', async () => {
		const parentRow = row('parent', 'Poster analysis', subjects.posterCandidates);
		parentRow.job_type = 'poster_pipeline_tv_batch';
		const showRow = row('show', 'Show child', subjects.series, true);
		const seasonRow = row('season', 'Season child', subjects.season);
		const store = fakeStore([record(parentRow), record(showRow), record(seasonRow)]);
		getStore.mockReturnValue(store);
		const onSettled = vi.fn<(snapshot: JobSnapshotResponse) => void>();

		render(FeatureActivityPanel, {
			props: {
				scopeKey: 'tv',
				queries: [
					{ type: 'poster_pipeline_tv_batch' },
					{ type: 'poster_pipeline', subject_kind: 'series' },
					{ type: 'poster_pipeline', subject_kind: 'season' }
				],
				jobIds: ['parent'],
				onSettled
			}
		});

		await waitFor(() => expect(screen.getByText('Poster analysis')).toBeVisible());
		expect(screen.getByText('Show child')).toBeVisible();
		expect(screen.getByText('Season child')).toBeVisible();
		expect(onSettled).not.toHaveBeenCalled();

		const terminalParent = row('parent', 'Poster analysis', subjects.posterCandidates, true);
		terminalParent.job_type = 'poster_pipeline_tv_batch';
		store.records.set('parent', record(terminalParent));

		await waitFor(() => expect(onSettled).toHaveBeenCalledTimes(1));
		expect(onSettled).toHaveBeenCalledWith(expect.objectContaining({ job_id: 'parent' }));
	});

	it('reports newer scope-discovered snapshots before the parent settles', async () => {
		const parentRow = row('parent', 'Poster analysis', subjects.posterCandidates);
		parentRow.job_type = 'poster_pipeline_tv_batch';
		const parentRecord = record(parentRow);
		const store = fakeStore([parentRecord]);
		getStore.mockReturnValue(store);
		const onUpdated = vi.fn<(snapshot: JobSnapshotResponse) => void>();

		render(FeatureActivityPanel, {
			props: {
				scopeKey: 'tv-updates',
				query: { type: 'poster_pipeline_tv_batch' },
				onUpdated
			}
		});

		await waitFor(() => expect(onUpdated).toHaveBeenCalledTimes(1));
		const first = parentRecord.snapshot!;
		store.records.set('parent', {
			...parentRecord,
			snapshot: { ...first, last_event_id: (first.last_event_id ?? 0) + 1 }
		});

		await waitFor(() => expect(onUpdated).toHaveBeenCalledTimes(2));
		expect(onUpdated).toHaveBeenLastCalledWith(
			expect.objectContaining({ job_id: 'parent', phase: 'running' })
		);
	});
});
