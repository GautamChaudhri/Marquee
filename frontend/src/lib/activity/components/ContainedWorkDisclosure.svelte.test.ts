import { render, screen, waitFor } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ContainedWorkItem, ContainedWorkPage, ContainedWorkSummary } from '../types';
import ContainedWorkDisclosure from './ContainedWorkDisclosure.svelte';

const { listContainedWork } = vi.hoisted(() => ({ listContainedWork: vi.fn() }));

vi.mock('../client', () => ({ listContainedWork }));

function summary(sequence = 3): ContainedWorkSummary {
	return {
		version: 1,
		source: 'child_jobs',
		label: 'Posters Being Healed',
		item_label_singular: 'subject',
		item_label_plural: 'subjects',
		total: 2,
		completed: 1,
		counts: {
			pending: 0,
			running: 1,
			retrying: 0,
			succeeded: 0,
			no_change: 0,
			review_required: 1,
			failed: 0,
			cancelled: 0
		},
		sequence,
		updated_at: '2026-07-31T12:00:00Z',
		href: '/api/jobs/heal-parent/contained-work'
	};
}

function item(key: string, ordinal: number): ContainedWorkItem {
	return {
		version: 1,
		key,
		ordinal,
		subject: {
			display_name: key.endsWith('next')
				? 'Next Movie'
				: ordinal === 0
					? 'Alpha Movie'
					: 'Beta Movie'
		},
		status: ordinal === 0 ? 'running' : 'review_required',
		status_label: ordinal === 0 ? 'Running' : 'Ready for review',
		status_tone: ordinal === 0 ? 'active' : 'warning',
		stage_key: 'restore',
		stage_name: 'Restoring poster',
		stage_number: 2,
		stage_total: 4,
		progress: { completed: 1, total: 2, unit: 'steps' },
		message: ordinal === 0 ? null : 'Choose a poster before restoring this subject.',
		sequence: 3,
		updated_at: '2026-07-31T12:00:00Z',
		detail_href: `/projection-room/jobs/${key}`
	};
}

function page(items: ContainedWorkItem[], nextCursor: string | null): ContainedWorkPage {
	return {
		version: 1,
		job_id: 'heal-parent',
		items,
		next_cursor: nextCursor,
		limit: 50,
		summary: summary(),
		historical_fallback: false
	};
}

/** The stage line is assembled from several text nodes; read it as one string. */
function textOf(selector: string): string[] {
	return [...document.querySelectorAll(selector)].map((node) =>
		(node.textContent ?? '').replace(/\s+/g, ' ').trim()
	);
}

const stageLines = () => textOf('.stage');
const measures = () => textOf('.measure');

describe('ContainedWorkDisclosure', () => {
	beforeEach(() => listContainedWork.mockReset());

	it('loads lazily and presents generic child stages, messages, progress, and diagnostics', async () => {
		listContainedWork.mockResolvedValue(
			page([item('heal-child-1', 0), item('heal-child-2', 1)], null)
		);
		const view = render(ContainedWorkDisclosure, {
			props: { jobId: 'heal-parent', summary: summary(), open: false }
		});
		expect(listContainedWork).not.toHaveBeenCalled();

		await view.rerender({ jobId: 'heal-parent', summary: summary(), open: true });
		await waitFor(() => expect(listContainedWork).toHaveBeenCalledTimes(1));
		expect(screen.getByText('Alpha Movie')).toBeVisible();
		expect(screen.getAllByText(/Stage 2 of 4/)).toHaveLength(2);
		expect(screen.getAllByRole('progressbar')).toHaveLength(2);
		expect(screen.getByText('Choose a poster before restoring this subject.')).toBeVisible();
		expect(screen.getAllByRole('link', { name: 'Details' })[1]).toHaveAttribute(
			'href',
			'/projection-room/jobs/heal-child-2'
		);
	});

	it('counts each subject against its own workload, not the group it was pooled with', async () => {
		// Two subjects part-way through the same pooled stage. The whole point of
		// the per-subject roster is that these two rows disagree.
		const rows: ContainedWorkItem[] = [
			{
				...item('group-member-0', 0),
				subject: { display_name: 'Deadpool' },
				stage_name: 'Validating',
				stage_number: 4,
				stage_total: 9,
				progress: { completed: 31, total: 38, unit: 'candidates' },
				source_count: 47,
				detail_href: null
			},
			{
				...item('group-member-1', 1),
				subject: { display_name: 'Black Widow' },
				status: 'running',
				status_label: 'Running',
				status_tone: 'active',
				stage_name: 'Validating',
				stage_number: 4,
				stage_total: 9,
				progress: { completed: 9, total: 22, unit: 'candidates' },
				source_count: 26,
				message: null,
				detail_href: null
			}
		];
		listContainedWork.mockResolvedValue(page(rows, null));

		render(ContainedWorkDisclosure, {
			props: { jobId: 'poster-group', summary: summary(), open: true }
		});

		await screen.findByText('Deadpool');
		expect(stageLines()).toEqual([
			'Stage 4 of 9 · Validating · 47 posters found',
			'Stage 4 of 9 · Validating · 26 posters found'
		]);
		expect(measures()).toEqual(['31 / 38 candidates', '9 / 22 candidates']);
		const bars = screen.getAllByRole('progressbar');
		expect(bars.map((bar) => bar.getAttribute('value'))).toEqual(['31', '9']);
		expect(bars.map((bar) => bar.getAttribute('max'))).toEqual(['38', '22']);
	});

	it('omits the source count until the download stage has reported one', async () => {
		listContainedWork.mockResolvedValue(
			page([{ ...item('group-member-0', 0), detail_href: null }], null)
		);
		render(ContainedWorkDisclosure, {
			props: { jobId: 'poster-group', summary: summary(), open: true }
		});
		await screen.findByText('Alpha Movie');
		expect(stageLines()).toEqual(['Stage 2 of 4 · Restoring poster']);
	});

	it('uses an opaque cursor when loading the next stable page', async () => {
		const user = userEvent.setup();
		listContainedWork
			.mockResolvedValueOnce(
				page(
					Array.from({ length: 50 }, (_value, ordinal) => item(`heal-child-${ordinal}`, ordinal)),
					'opaque-page-2'
				)
			)
			.mockResolvedValueOnce(page([item('heal-child-next', 50)], null));

		render(ContainedWorkDisclosure, {
			props: { jobId: 'heal-parent', summary: summary(), open: true }
		});
		await user.click(await screen.findByRole('button', { name: 'Load 50 more' }));
		expect(listContainedWork).toHaveBeenNthCalledWith(
			2,
			expect.any(Function),
			'heal-parent',
			{ cursor: 'opaque-page-2', limit: 50 },
			expect.any(AbortSignal)
		);
		expect(await screen.findByText('Next Movie')).toBeVisible();
	});
});
