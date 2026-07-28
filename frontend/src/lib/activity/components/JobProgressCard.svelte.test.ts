import { render, screen } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import JobProgressCard from './JobProgressCard.svelte';
import { makePresentation, makeRow, makeSnapshot, subjects } from './fixtures';

describe('subject and terminal golden matrix', () => {
	for (const [kind, subject] of Object.entries(subjects)) {
		it(`renders the ${kind} subject hierarchy`, () => {
			render(JobProgressCard, { props: { row: makeRow({ subject }) } });
			expect(screen.getByRole('heading', { name: subject.display_name })).toBeInTheDocument();
			for (const context of subject.context) {
				expect(document.querySelector('.context')).toHaveTextContent(context);
			}
		});
	}

	for (const fixture of [
		['success', 'succeeded', 'Complete', 'positive'],
		['no-change', 'no_change', 'No changes needed', 'positive'],
		['failure', 'failed', 'Failed', 'negative'],
		['cancellation', 'cancelled', 'Cancelled', 'warning']
	] as const) {
		it(`preserves progress for terminal ${fixture[0]}`, () => {
			const row = makeRow({
				status: {
					label: fixture[2],
					label_key: `jobs.status.${fixture[1]}`,
					phase: 'terminal',
					outcome: fixture[1],
					tone: fixture[3]
				},
				terminal_at: '2026-07-16T12:02:00Z'
			});
			render(JobProgressCard, { props: { row } });
			expect(screen.getByText(fixture[2], { selector: '.status' })).toBeVisible();
			expect(screen.getByRole('progressbar', { name: 'Overall' })).toHaveAttribute(
				'aria-valuenow',
				'37'
			);
		});
	}

	it('marks a deleted subject without losing its snapshot identity', () => {
		const deleted = { ...subjects.movie, missing_live_subject: true };
		render(JobProgressCard, { props: { row: makeRow({ subject: deleted }) } });
		expect(screen.getByText('Dune: Part Two')).toBeVisible();
		expect(screen.getByText(/source item no longer exists/i)).toBeVisible();
	});
});

describe('honest progress goldens', () => {
	it('uses the server supplied determinate percent without deriving a value', () => {
		render(JobProgressCard, { props: { row: makeRow() } });
		const meter = screen.getByRole('progressbar', { name: 'Overall' });
		expect(meter).toHaveAttribute('aria-valuenow', '37');
		expect(meter.firstElementChild).toHaveStyle({ width: '37%' });
		expect(screen.getByText('3 / 8 steps')).toBeVisible();
	});

	it('renders indeterminate work without a fabricated value', () => {
		const row = makeRow();
		row.progress = {
			...row.progress,
			overall: { scope_id: 'scan', mode: 'indeterminate', label: 'Scanning library' }
		};
		render(JobProgressCard, { props: { row } });
		expect(screen.getByRole('progressbar', { name: 'Scanning library' })).not.toHaveAttribute(
			'aria-valuenow'
		);
	});

	it('renders hybrid overall and resettable current scopes independently', () => {
		const row = makeRow();
		row.progress = {
			...row.progress,
			current_subject: subjects.episode,
			current: { scope_id: 'episode-1', mode: 'indeterminate', label: 'Analyzing episode' }
		};
		render(JobProgressCard, { props: { row } });
		expect(screen.getAllByRole('progressbar')).toHaveLength(2);
		expect(screen.getByText('Now: Hello, Ms. Cobel')).toBeVisible();
	});

	it('renders server-reported execution I/O without estimating it', () => {
		const row = makeRow();
		row.progress = {
			...row.progress,
			metrics: {
				bytes_processed: 2 * 1024 * 1024,
				bytes_total: 4 * 1024 * 1024,
				throughput: 512 * 1024
			}
		};
		render(JobProgressCard, { props: { row } });
		expect(screen.getByText('2.0 MiB of 4.0 MiB · 512 KiB/s')).toBeVisible();
	});

	it('renders an immediate operation with no progress bar', () => {
		const row = makeRow({ progress: { sequence: 1, headline: 'Applying setting' } });
		render(JobProgressCard, { props: { row } });
		expect(screen.getByText('Applying setting')).toBeVisible();
		expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
	});
});

describe('attention, freshness, evidence, and concurrent work', () => {
	it.each([
		['retrying', 'Retrying'],
		['held', 'Paused'],
		['waiting', 'Waiting']
	] as const)('renders the %s server attention as %s', (reason, label) => {
		const row = makeRow({
			attention: {
				level: 'warning',
				reason,
				message: 'The server will continue this job.',
				remediation: null
			}
		});
		render(JobProgressCard, { props: { row } });
		expect(screen.getByText(label)).toBeVisible();
		expect(screen.getByText('The server will continue this job.')).toBeVisible();
	});

	it('shows the authoritative retry eligibility without inventing an ETA', () => {
		const row = makeRow();
		row.progress = {
			...row.progress,
			wait: {
				kind: 'retry_backoff',
				label_key: 'jobs.wait.retry_backoff',
				eligible_at: '2026-07-16T12:05:00Z'
			}
		};
		render(JobProgressCard, { props: { row } });
		expect(screen.getByText('Waiting')).toBeVisible();
		expect(screen.getByText('Eligible at 2026-07-16T12:05:00Z')).toBeVisible();
	});

	it('uses text and tone together for error attention', () => {
		const row = makeRow({
			attention: {
				level: 'error',
				reason: 'needs_input',
				message: 'Choose a replacement file.',
				remediation: null
			}
		});
		render(JobProgressCard, { props: { row } });
		expect(screen.getByText('Needs attention')).toBeVisible();
		expect(document.querySelector('.callout')).toHaveAttribute('data-tone', 'negative');
	});

	it('renders cancelling from the authoritative stopping phase', () => {
		const row = makeRow({ status: { ...makeRow().status, phase: 'stopping', label: 'Stopping' } });
		render(JobProgressCard, { props: { row } });
		expect(screen.getByText('Cancelling')).toBeVisible();
	});

	it.each([
		['stale', 'Progress may be stale'],
		['reconnecting', 'Reconnecting']
	] as const)('renders the %s connection state', (connection, label) => {
		render(JobProgressCard, { props: { row: makeRow(), connection } });
		expect(screen.getByText(label)).toBeVisible();
	});

	it('renders only server-provided typed metrics in the expanded card', () => {
		const row = makeRow();
		render(JobProgressCard, {
			props: { row, presentation: makePresentation(row), variant: 'expanded' }
		});
		expect(screen.getByRole('region', { name: 'Job metrics' })).toHaveTextContent(/Elapsed\s+12 s/);
		expect(screen.getByText('24 frames/s')).toBeVisible();
	});

	it('bounds concurrent children and signals additional work', () => {
		const children = ['One', 'Two', 'Three', 'Four'].map((name, index) =>
			makeRow({
				job_id: `child-${index}`,
				subject: { ...subjects.episode, display_id: `${index}`, display_name: name }
			})
		);
		render(JobProgressCard, {
			props: { row: makeRow(), children, childrenHasMore: true, variant: 'expanded' }
		});
		expect(screen.getByText('One')).toBeVisible();
		expect(screen.getByText('Three')).toBeVisible();
		expect(screen.queryByText('Four')).not.toBeInTheDocument();
		expect(screen.getByText('1 more loaded')).toBeVisible();
	});
});

describe('actions, keyboard, and restrained announcements', () => {
	it('exposes typed diagnostic links and submits cancel with the canonical fence', async () => {
		const user = userEvent.setup();
		const onCancel = vi.fn();
		const row = makeRow();
		const snapshot = makeSnapshot(row, {
			allowed_actions: ['cancel', 'open_detail', 'open_logs', 'open_artifacts']
		});
		render(JobProgressCard, {
			props: {
				row,
				snapshot,
				presentation: makePresentation(row),
				variant: 'expanded',
				onCancel
			}
		});
		expect(screen.getByRole('link', { name: 'Activity' })).toHaveAttribute(
			'href',
			'/projection-room'
		);
		expect(screen.getByRole('link', { name: 'Logs' })).toHaveAttribute(
			'href',
			'/api/jobs/job-public-1/attempts'
		);
		expect(screen.getByRole('link', { name: 'Artifacts' })).toBeVisible();

		await user.tab();
		expect(screen.getByRole('link', { name: 'Activity' })).toHaveFocus();
		await user.tab();
		expect(screen.getByRole('link', { name: 'Details' })).toHaveFocus();
		await user.tab();
		await user.tab();
		await user.tab();
		expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus();
		await user.keyboard('{Enter}');
		expect(onCancel).toHaveBeenCalledWith('job-public-1', 7);
	});

	it('does not offer an unfenced cancel command', () => {
		render(JobProgressCard, { props: { row: makeRow(), onCancel: vi.fn() } });
		expect(screen.queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument();
	});

	it('announces subject, action, status, and friendly stage but not high-frequency ticks', async () => {
		const row = makeRow();
		const view = render(JobProgressCard, { props: { row } });
		const live = document.querySelector('[aria-live="polite"]');
		expect(live).toHaveTextContent(
			'Dune: Part Two. Selecting the best artwork. Running. Comparing artwork'
		);
		expect(live).not.toHaveTextContent('37');

		const currentProgress = row.progress!;
		const overall = currentProgress.overall!;
		const next = makeRow({
			progress: { ...currentProgress, overall: { ...overall, percent: 81 } }
		});
		await view.rerender({ row: next });
		expect(live).not.toHaveTextContent('81');
	});

	it('keeps action targets usable at a narrow viewport', () => {
		Object.defineProperty(window, 'innerWidth', { configurable: true, value: 360 });
		render(JobProgressCard, { props: { row: makeRow() } });
		for (const link of screen.getAllByRole('link')) expect(link).toBeVisible();
		expect(document.querySelector('article')).toHaveClass('card');
	});
});

describe('settled jobs stop advertising work in flight', () => {
	// Reproduces a real History card: a terminal poster_pipeline that kept
	// rendering "6 / 9 stages" plus an endlessly scanning "Now:" bar. The server
	// retains those values on purpose and flags them `freshness: 'terminal'`;
	// the card has to read that rather than draw them as live.
	function terminalRow(progressOverrides = {}) {
		const base = makeRow();
		return makeRow({
			status: {
				label: 'Partially succeeded',
				label_key: 'jobs.status.partially_succeeded',
				phase: 'terminal',
				outcome: 'partially_succeeded',
				tone: 'warning'
			},
			terminal_at: '2026-07-16T12:02:00Z',
			progress: {
				...base.progress!,
				freshness: 'terminal',
				current_subject: subjects.season,
				current: { scope_id: 'season-1', mode: 'indeterminate', label: 'Finalizing' },
				...progressOverrides
			}
		});
	}

	it('drops the present-tense current-work block once the job is over', () => {
		render(JobProgressCard, { props: { row: terminalRow() } });
		expect(screen.queryByText(/^Now:/)).not.toBeInTheDocument();
		expect(document.querySelector('.current-work')).not.toBeInTheDocument();
	});

	it('never leaves a scanning indeterminate track on a finished job', () => {
		render(JobProgressCard, { props: { row: terminalRow() } });
		expect(document.querySelector('.track.indeterminate')).not.toBeInTheDocument();
	});

	it('still shows where a failed job actually stopped', () => {
		render(JobProgressCard, { props: { row: terminalRow() } });
		// The retained determinate measure is evidence and must survive.
		expect(screen.getByRole('progressbar', { name: 'Overall' })).toHaveAttribute(
			'aria-valuenow',
			'37'
		);
		expect(screen.getByText('3 / 8 steps')).toBeVisible();
	});

	it('settles on the record partition even if the row still claims to be running', () => {
		// History rows are marked terminal by the store; a stale row that still
		// says "running" must not reanimate the card.
		const base = makeRow();
		const row = makeRow({
			progress: {
				...base.progress!,
				current_subject: subjects.season,
				current: { scope_id: 'season-1', mode: 'indeterminate', label: 'Finalizing' }
			}
		});
		render(JobProgressCard, { props: { row, recordFreshness: 'terminal' } });
		expect(document.querySelector('.track.indeterminate')).not.toBeInTheDocument();
		expect(screen.queryByText(/^Now:/)).not.toBeInTheDocument();
	});

	it('leaves a genuinely running job animating', () => {
		const base = makeRow();
		const row = makeRow({
			progress: {
				...base.progress!,
				current_subject: subjects.season,
				current: { scope_id: 'season-1', mode: 'indeterminate', label: 'Finalizing' }
			}
		});
		render(JobProgressCard, { props: { row } });
		expect(screen.getByText(`Now: ${subjects.season.display_name}`)).toBeVisible();
		expect(document.querySelector('.track.indeterminate')).toBeInTheDocument();
	});
});
