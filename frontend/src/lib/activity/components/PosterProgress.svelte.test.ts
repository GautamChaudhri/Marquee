import { render, screen, waitFor } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { WorkItemPage, WorkItemRow, WorkItemSummary } from '../types';
import PosterProgress from './PosterProgress.svelte';

const { listWorkItems } = vi.hoisted(() => ({ listWorkItems: vi.fn() }));

vi.mock('../client', () => ({ listWorkItems }));

function summary(sequence = 1, counts: Partial<WorkItemSummary['counts']> = {}): WorkItemSummary {
	return {
		version: 1,
		total: 90,
		counts: {
			pending: 88,
			running: 1,
			succeeded: 0,
			no_change: 0,
			review_required: 0,
			failed: 1,
			cancelled: 0,
			...counts
		},
		sequence,
		updated_at: '2026-07-30T12:00:00Z',
		href: '/api/jobs/poster-group/work-items'
	};
}

function item(ordinal: number, overrides: Partial<WorkItemRow> = {}): WorkItemRow {
	return {
		version: 1,
		subject_key: `movie:${ordinal + 1}`,
		subject_kind: 'movie',
		subject_reference: `${ordinal + 1}`,
		subject: {
			display_name: `Movie ${ordinal + 1}`,
			title: `Movie ${ordinal + 1}`
		},
		ordinal,
		status: 'running',
		stage_key: 'gate-resolution',
		stage_name: 'Validating',
		stage_number: 4,
		stage_total: 9,
		progress: { completed: 2, total: 10, unit: 'candidates' },
		message: null,
		sequence: 1,
		updated_at: '2026-07-30T12:00:00Z',
		...overrides
	};
}

function page(
	items: WorkItemRow[],
	nextCursor: number | null,
	pageSummary = summary()
): WorkItemPage {
	return {
		version: 1,
		job_id: 'poster-group',
		items,
		next_cursor: nextCursor,
		limit: 50,
		summary: pageSummary,
		historical_fallback: false
	};
}

describe('PosterProgress', () => {
	beforeEach(() => listWorkItems.mockReset());

	it('loads the first 50 stable rows only when disclosed and shows exact progress', async () => {
		const user = userEvent.setup();
		listWorkItems.mockResolvedValue(
			page(
				Array.from({ length: 50 }, (_value, ordinal) => item(ordinal)),
				50
			)
		);

		render(PosterProgress, { props: { jobId: 'poster-group', summary: summary() } });
		expect(listWorkItems).not.toHaveBeenCalled();
		expect(screen.getByText('90 posters')).toBeVisible();
		expect(screen.getByLabelText('Poster progress summary')).toHaveTextContent('1 running');
		expect(screen.getByLabelText('Poster progress summary')).toHaveTextContent(
			'1 poster needs attention'
		);

		await user.click(screen.getByText('Poster progress'));

		await waitFor(() => expect(listWorkItems).toHaveBeenCalledTimes(1));
		expect(listWorkItems).toHaveBeenCalledWith(
			expect.any(Function),
			'poster-group',
			{ cursor: undefined, limit: 50 },
			expect.any(AbortSignal)
		);
		expect(screen.getByText('Movie 1')).toBeVisible();
		expect(screen.getAllByText('Stage 4 of 9 · Validating')[0]).toBeVisible();
		expect(screen.getAllByText('2 of 10 candidates')[0]).toBeVisible();
		expect(screen.getAllByRole('progressbar')[0]).toHaveAttribute('value', '2');
	});

	it('paginates by stable ordinal and refreshes every loaded row after a sequence advance', async () => {
		const user = userEvent.setup();
		listWorkItems
			.mockResolvedValueOnce(
				page(
					Array.from({ length: 50 }, (_value, ordinal) => item(ordinal)),
					50
				)
			)
			.mockResolvedValueOnce(page([item(50)], null))
			.mockResolvedValueOnce(
				page(
					[
						item(0, { status: 'succeeded', stage_name: 'Finalizing', stage_number: 9 }),
						item(50, { status: 'review_required', message: 'Choose a poster to continue.' })
					],
					null,
					summary(2, { pending: 0, running: 0, succeeded: 89, review_required: 1, failed: 0 })
				)
			);

		const view = render(PosterProgress, {
			props: { jobId: 'poster-group', summary: summary() }
		});
		await user.click(screen.getByText('Poster progress'));
		await user.click(await screen.findByRole('button', { name: 'Load 50 more' }));

		await waitFor(() => expect(listWorkItems).toHaveBeenCalledTimes(2));
		expect(listWorkItems).toHaveBeenNthCalledWith(
			2,
			expect.any(Function),
			'poster-group',
			{ cursor: 50, limit: 50 },
			expect.any(AbortSignal)
		);
		expect(screen.getByText('Movie 51')).toBeVisible();

		await view.rerender({ jobId: 'poster-group', summary: summary(2) });
		await waitFor(() => expect(listWorkItems).toHaveBeenCalledTimes(3));
		expect(screen.getByText('Stage 9 of 9 · Finalizing')).toBeVisible();
		expect(screen.getByText('Ready for review')).toBeVisible();
		expect(screen.getByText('Choose a poster to continue.')).toBeVisible();
	});

	it('surfaces a specific loading failure with an accessible retry', async () => {
		const user = userEvent.setup();
		listWorkItems
			.mockRejectedValueOnce(new Error('Poster rows are temporarily unavailable.'))
			.mockResolvedValueOnce(page([item(0)], null));

		render(PosterProgress, { props: { jobId: 'poster-group', summary: summary() } });
		await user.click(screen.getByText('Poster progress'));
		expect(await screen.findByRole('alert')).toHaveTextContent(
			'Poster rows are temporarily unavailable.'
		);
		await user.click(screen.getByRole('button', { name: 'Try again' }));
		expect(await screen.findByText('Movie 1')).toBeVisible();
	});
});
