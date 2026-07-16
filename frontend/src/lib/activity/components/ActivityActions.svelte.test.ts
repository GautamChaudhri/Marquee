import { fireEvent, render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';
import ActivityActions from './ActivityActions.svelte';
import { makeRow, makeSnapshot } from './fixtures';

describe('ActivityActions', () => {
	it('renders only server-returned capabilities', () => {
		const row = makeRow({ allowed_actions: ['pause', 'open_logs', 'open_detail'] });
		render(ActivityActions, { row, onCommand: vi.fn() });

		expect(screen.getByRole('button', { name: 'Pause' })).toBeVisible();
		expect(screen.getByRole('link', { name: 'Logs' })).toHaveAttribute(
			'href',
			`${row.links.detail}?tab=logs`
		);
		expect(screen.queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument();
		expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument();
	});

	it('shows the canonical retry successor returned by the server', async () => {
		const row = makeRow({
			status: {
				label: 'Failed',
				label_key: 'jobs.status.failed',
				phase: 'terminal',
				outcome: 'failed',
				tone: 'negative'
			},
			allowed_actions: ['retry', 'open_detail']
		});
		const replacement = makeRow({ job_id: 'replacement00000000000000000001' });
		const onCommand = vi.fn().mockResolvedValue({
			action: 'retry',
			execution_class: row.execution_class,
			snapshot: makeSnapshot(replacement, { retry_of_job_id: row.job_id }),
			original_job_id: row.job_id,
			replacement_job_id: replacement.job_id
		});
		render(ActivityActions, { row, onCommand });

		await fireEvent.click(screen.getByRole('button', { name: 'Retry' }));

		expect(onCommand).toHaveBeenCalledWith(row, 'retry', undefined);
		expect(await screen.findByRole('link', { name: 'Open retry successor' })).toHaveAttribute(
			'href',
			`/projection-room/jobs/${replacement.job_id}`
		);
	});
});
