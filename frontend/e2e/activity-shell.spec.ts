import { expect, test } from '@playwright/test';

test('renders the canonical Activity shell', async ({ page }) => {
	await page.addInitScript(() => {
		class StableEventSource {
			addEventListener(type: string, listener: (event: Event) => void) {
				if (type === 'open') queueMicrotask(() => listener(new Event('open')));
			}
			close() {}
		}
		Object.defineProperty(window, 'EventSource', { value: StableEventSource });
	});
	await page.setViewportSize({ width: 1280, height: 900 });
	await page.goto('/projection-room');
	await expect(page.getByRole('heading', { name: 'Activity', exact: true })).toBeVisible();
	await expect(page.getByRole('link', { name: 'Activity' })).toHaveCount(1);
	await expect(page.getByText('Nothing is waiting.')).toBeVisible();
});

test('restores URL-backed Queue and History state across navigation', async ({ page }) => {
	await page.goto('/projection-room');
	await page.getByRole('button', { name: /History/ }).click();
	await expect(page).toHaveURL(/view=history/);
	await page.getByLabel('Search subjects').fill('Dune');
	await page.getByRole('button', { name: 'Apply filters' }).click();
	await expect(page).toHaveURL(/q=Dune/);

	await page.getByRole('button', { name: /Queue/ }).click();
	await expect(page).toHaveURL(/view=queue/);
	await page.goBack();
	await expect(page).toHaveURL(/view=history.*q=Dune|q=Dune.*view=history/);
	await expect(page.getByLabel('Search subjects')).toHaveValue('Dune');
});

test('keeps primary Activity controls usable at phone width', async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto('/projection-room');
	await expect(page.getByRole('button', { name: /Queue/ })).toBeVisible();
	await expect(page.getByRole('button', { name: /History/ })).toBeVisible();
	await expect(page.getByLabel('Search subjects')).toBeVisible();
});

test('uses server capabilities and displays a retry successor', async ({ page }) => {
	const jobId = 'failed00000000000000000000000001';
	const replacementId = 'retry20000000000000000000000001';
	const row = {
		version: 1,
		job_id: jobId,
		job_type: 'system_noop',
		label: 'System no-op',
		label_key: 'jobs.system_noop',
		feature_area: 'system',
		presentation_family: 'system',
		subject: {
			kind: 'system_work',
			display_id: 'system:test',
			display_name: 'Synthetic failed job',
			artwork_key: null,
			context: ['E2E fixture'],
			snapshot_at: '2026-07-16T12:00:00Z',
			missing_live_subject: false
		},
		action_headline: 'Exercise capability actions',
		status: {
			label: 'Failed',
			label_key: 'jobs.status.failed',
			phase: 'terminal',
			outcome: 'failed',
			tone: 'negative'
		},
		trigger: { kind: 'system', label: 'System initiated', initiator: null },
		attention: {
			level: 'error',
			reason: 'failed',
			message: 'Synthetic failure',
			remediation: null
		},
		progress: null,
		impact: null,
		allowed_actions: ['retry', 'open_logs', 'open_detail'],
		is_parent: false,
		parent_id: null,
		root_id: jobId,
		retry_of_job_id: null,
		fence_token: 4,
		execution_class: 'control',
		priority: 50,
		queue_rank: null,
		eligible_at: null,
		created_at: '2026-07-16T12:00:00Z',
		started_at: '2026-07-16T12:00:01Z',
		terminal_at: '2026-07-16T12:00:02Z',
		duration_seconds: 1,
		evidence: { logs_available: true, artifacts_available: false },
		links: {
			detail: `/projection-room/jobs/${jobId}`,
			snapshot: `/api/jobs/${jobId}/snapshot`,
			presentation: `/api/jobs/${jobId}/presentation`
		}
	};
	await page.route(/\/api\/jobs\?.*/, async (route) => {
		await route.fulfill({ json: { view: 'history', items: [row], next_cursor: null, limit: 50 } });
	});
	await page.route(`**/api/jobs/${jobId}/retry`, async (route) => {
		expect(route.request().postDataJSON()).toEqual({ expected_fence_token: 4 });
		await route.fulfill({
			json: {
				action: 'retry',
				execution_class: 'control',
				original_job_id: jobId,
				replacement_job_id: replacementId,
				snapshot: {
					version: 1,
					job_id: replacementId,
					type: 'system_noop',
					phase: 'queued',
					outcome: null,
					desired_state: 'run',
					fence_token: 0,
					progress_sequence: 0,
					progress: null
				}
			}
		});
	});

	await page.goto('/projection-room?view=history&q=synthetic');
	await expect(page.getByRole('button', { name: 'Retry' })).toBeVisible();
	await expect(page.getByRole('link', { name: 'Logs' })).toHaveAttribute(
		'href',
		`/projection-room/jobs/${jobId}?tab=logs`
	);
	await expect(page.getByRole('button', { name: 'Cancel' })).toHaveCount(0);
	await page.getByRole('button', { name: 'Retry' }).click();
	await expect(page.getByRole('link', { name: 'Open retry successor' })).toHaveAttribute(
		'href',
		`/projection-room/jobs/${replacementId}`
	);
});
