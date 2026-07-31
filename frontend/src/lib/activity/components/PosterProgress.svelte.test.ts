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
		listWorkItems.mockResolvedValue(
			page(
				Array.from({ length: 50 }, (_value, ordinal) => item(ordinal)),
				50
			)
		);

		// The toggle lives in the row's control bar now, so the panel is told whether it
		// is open rather than deciding for itself. Closed still means no request.
		const view = render(PosterProgress, {
			props: { jobId: 'poster-group', summary: summary(), open: false }
		});
		expect(listWorkItems).not.toHaveBeenCalled();
		// The outcome counts moved onto the card. Burying the result of a run inside a
		// collapsed disclosure was the one place it was least likely to be read.
		expect(screen.queryByLabelText('Poster progress summary')).not.toBeInTheDocument();

		await view.rerender({ jobId: 'poster-group', summary: summary(), open: true });

		await waitFor(() => expect(listWorkItems).toHaveBeenCalledTimes(1));
		expect(listWorkItems).toHaveBeenCalledWith(
			expect.any(Function),
			'poster-group',
			{ cursor: undefined, limit: 50 },
			expect.any(AbortSignal)
		);
		// A roster of what is in the chunk, nothing more. Every row used to repeat the
		// same stage, status word and bar the parent card already shows.
		expect(screen.getByText('Movie 1')).toBeVisible();
		expect(screen.queryByText(/Stage \d+ of \d+/)).not.toBeInTheDocument();
		expect(screen.queryByText(/candidates$/)).not.toBeInTheDocument();
		expect(screen.queryAllByRole('progressbar')).toHaveLength(0);
	});

	it('withholds colour from rows that have not settled', async () => {
		listWorkItems.mockResolvedValue(
			page(
				[
					item(0, { status: 'running' }),
					item(1, { status: 'pending' }),
					item(2, { status: 'succeeded' }),
					item(3, { status: 'review_required' })
				],
				null
			)
		);

		render(PosterProgress, { props: { jobId: 'poster-group', summary: summary(), open: true } });
		await screen.findByText('Movie 1');

		const rows = [...document.querySelectorAll('.item')];
		expect(rows.map((row) => row.classList.contains('settled'))).toEqual([
			false,
			false,
			true,
			true
		]);
	});

	it('explains yellow and red outcomes while keeping other settled rows compact', async () => {
		listWorkItems.mockResolvedValue(
			page(
				[
					item(0, {
						status: 'review_required',
						message: 'Choose a poster to teach Marquee your preferences.'
					}),
					item(1, {
						status: 'failed',
						message: 'No poster files were downloaded or found in the cache.'
					}),
					item(2, { status: 'no_change', message: 'No viable poster change was found.' }),
					item(3, { status: 'succeeded', message: 'Poster analysis completed.' })
				],
				null
			)
		);

		render(PosterProgress, { props: { jobId: 'poster-group', summary: summary(), open: true } });

		expect(
			await screen.findByText('Choose a poster to teach Marquee your preferences.')
		).toBeVisible();
		expect(
			screen.getByText('No poster files were downloaded or found in the cache.')
		).toBeVisible();
		expect(screen.queryByText('No viable poster change was found.')).not.toBeInTheDocument();
		expect(screen.queryByText('Poster analysis completed.')).not.toBeInTheDocument();
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
						item(50, { status: 'failed', message: 'TMDB returned no usable artwork.' })
					],
					null,
					summary(2, { pending: 0, running: 0, succeeded: 89, review_required: 0, failed: 1 })
				)
			);

		const view = render(PosterProgress, {
			props: { jobId: 'poster-group', summary: summary(), open: true }
		});
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

		await view.rerender({ jobId: 'poster-group', summary: summary(2), open: true });
		await waitFor(() => expect(listWorkItems).toHaveBeenCalledTimes(3));
		expect(screen.queryByText(/Stage \d+ of \d+/)).not.toBeInTheDocument();
		// Boilerplate messages are dropped, but a failure is unreadable without its reason.
		expect(screen.getByText('TMDB returned no usable artwork.')).toBeVisible();
	});

	it('surfaces a specific loading failure with an accessible retry', async () => {
		const user = userEvent.setup();
		listWorkItems
			.mockRejectedValueOnce(new Error('Poster rows are temporarily unavailable.'))
			.mockResolvedValueOnce(page([item(0)], null));

		render(PosterProgress, { props: { jobId: 'poster-group', summary: summary(), open: true } });
		expect(await screen.findByRole('alert')).toHaveTextContent(
			'Poster rows are temporarily unavailable.'
		);
		await user.click(screen.getByRole('button', { name: 'Try again' }));
		expect(await screen.findByText('Movie 1')).toBeVisible();
	});
});
