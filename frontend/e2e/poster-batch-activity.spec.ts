import { expect, test, type Page } from '@playwright/test';

const now = '2026-07-30T19:00:00Z';

function groupRow(
	jobId: string,
	title: string,
	library: 'movies' | 'tv',
	chunkIndex: number,
	chunkTotal: number | null = null,
	batchMode: 'chunked' | 'all_at_once' = 'chunked'
) {
	return {
		version: 1,
		job_id: jobId,
		job_type: 'poster_pipeline_group',
		label: title,
		label_key: 'jobs.poster_pipeline_group.label',
		feature_area: 'ai_posters',
		feature_label: 'Posters',
		presentation_family: 'ai_posters',
		subject: {
			kind: 'poster_subject_group',
			display_id: `poster-group:${library}:${chunkIndex}`,
			display_name: title,
			artwork_key: null,
			monogram: library === 'movies' ? 'FP' : 'TVP',
			context:
				batchMode === 'all_at_once'
					? ['Unified run']
					: [
							'Batch 7F3A',
							...(chunkTotal == null ? [] : [`Group ${chunkIndex + 1} of ${chunkTotal}`])
						],
			snapshot_at: now,
			missing_live_subject: false
		},
		action_headline: 'Select posters for a processing group',
		status: {
			label: 'Running',
			label_key: 'jobs.status.running',
			phase: 'running',
			outcome: null,
			tone: 'active'
		},
		attention: { level: 'normal', reason: 'none', message: null, remediation: null },
		trigger: { kind: 'batch', label: 'Started as a poster batch', initiator: null },
		progress: {
			sequence: 7,
			headline: 'Validating',
			stage_key: 'validating',
			stage_label: 'Validating',
			freshness: 'live',
			updated_at: now,
			overall: {
				scope_id: jobId,
				mode: 'determinate',
				label: 'Stages',
				percent: 44.4,
				completed: 4,
				total: 9,
				unit: 'stages'
			},
			current: null,
			current_subject: null,
			wait: null
		},
		work_items: {
			version: 1,
			total: 2,
			counts: {
				pending: 1,
				running: 1,
				succeeded: 0,
				no_change: 0,
				review_required: 0,
				failed: 0,
				cancelled: 0
			},
			sequence: 7,
			updated_at: now,
			href: `/api/jobs/${jobId}/work-items`
		},
		contained_work: {
			version: 1,
			source: 'work_items',
			label: 'Posters in This Group',
			item_label_singular: 'subject',
			item_label_plural: 'subjects',
			total: 2,
			completed: 0,
			counts: {
				pending: 1,
				running: 1,
				retrying: 0,
				succeeded: 0,
				no_change: 0,
				review_required: 0,
				failed: 0,
				cancelled: 0
			},
			sequence: 7,
			updated_at: now,
			href: `/api/jobs/${jobId}/contained-work`
		},
		priority: 50,
		fence_token: 3,
		execution_class: 'gpu',
		queue_rank: chunkIndex + 1,
		is_parent: false,
		allowed_actions: ['cancel', 'open_detail'],
		links: {
			detail: `/projection-room/jobs/${jobId}`,
			presentation: `/api/jobs/${jobId}/presentation`,
			snapshot: `/api/jobs/${jobId}/snapshot`
		},
		parent_id: 'hiddenparent00000000000000000001',
		root_id: 'hiddenparent00000000000000000001',
		retry_of_job_id: null,
		created_at: now,
		eligible_at: null,
		started_at: now,
		terminal_at: null,
		duration_seconds: null,
		impact: null,
		evidence: { artifacts_available: false, logs_available: false }
	};
}

function containedWorkPage(jobId: string) {
	return {
		version: 1,
		job_id: jobId,
		summary: {
			version: 1,
			source: 'work_items',
			label: 'Posters in This Group',
			item_label_singular: 'subject',
			item_label_plural: 'subjects',
			total: 2,
			completed: 0,
			counts: {
				pending: 1,
				running: 1,
				retrying: 0,
				succeeded: 0,
				no_change: 0,
				review_required: 0,
				failed: 0,
				cancelled: 0
			},
			sequence: 7,
			updated_at: now,
			href: `/api/jobs/${jobId}/contained-work`
		},
		items: [
			{
				version: 1,
				key: 'movie:1',
				ordinal: 0,
				subject: { display_name: 'Arrival' },
				status: 'running',
				status_label: 'Running',
				status_tone: 'active',
				stage_key: 'validating',
				stage_name: 'Validating',
				stage_number: 4,
				stage_total: 9,
				progress: { completed: 3, total: 8, unit: 'candidates' },
				message: 'Checking image dimensions.',
				sequence: 7,
				updated_at: now
			},
			{
				version: 1,
				key: 'movie:2',
				ordinal: 1,
				subject: { display_name: 'Blade Runner 2049' },
				status: 'pending',
				status_label: 'Pending',
				status_tone: 'neutral',
				stage_key: null,
				stage_name: null,
				stage_number: null,
				stage_total: 9,
				progress: null,
				message: null,
				sequence: 7,
				updated_at: now
			}
		],
		next_cursor: null,
		limit: 50,
		historical_fallback: false
	};
}

async function installStableEventSource(page: Page) {
	await page.addInitScript(() => {
		class StableEventSource {
			addEventListener(type: string, listener: (event: Event) => void) {
				if (type === 'open') queueMicrotask(() => listener(new Event('open')));
			}
			close() {}
		}
		Object.defineProperty(window, 'EventSource', { value: StableEventSource });
	});
}

test('shows one promoted card per poster execution unit with lazy per-poster progress', async ({
	page
}) => {
	await installStableEventSource(page);
	const rows = [
		groupRow('a1000000000000000000000000000001', 'Get Film Posters · 2 Subjects', 'movies', 0),
		groupRow('a2000000000000000000000000000002', 'Get Television Posters · 2 Subjects', 'tv', 0),
		groupRow('a3000000000000000000000000000003', 'Get Film Posters · 2 Subjects', 'movies', 0, 4),
		groupRow('a4000000000000000000000000000004', 'Get Television Posters · 2 Subjects', 'tv', 1, 4)
	];
	let workItemRequests = 0;
	const listRequest = page.waitForRequest(
		(request) => new URL(request.url()).pathname === '/api/jobs'
	);
	await page.route('**/api/jobs?*', (route) =>
		route.fulfill({
			json: { view: 'queue', items: rows, next_cursor: null, limit: 50 }
		})
	);
	await page.route(/\/api\/jobs\/[^/]+\/contained-work(?:\?.*)?$/, (route) => {
		workItemRequests += 1;
		const match = new URL(route.request().url()).pathname.match(
			/\/api\/jobs\/([^/]+)\/contained-work/
		);
		return route.fulfill({ json: containedWorkPage(match?.[1] ?? rows[0].job_id) });
	});

	await page.goto('/projection-room');
	const requestUrl = new URL((await listRequest).url());
	expect(requestUrl.searchParams.get('hierarchy')).toBe('activity');
	await expect(page.locator('article.activity-row')).toHaveCount(4);
	await expect(page.getByRole('heading', { name: 'Get Film Posters · 2 Subjects' })).toHaveCount(2);
	await expect(
		page.getByRole('heading', { name: 'Get Television Posters · 2 Subjects' })
	).toHaveCount(2);
	await expect(page.getByText('Batch 7F3A · Group 1 of 4')).toBeVisible();
	await expect(page.getByText('Batch 7F3A · Group 2 of 4')).toBeVisible();
	// The stage now labels the progress bar itself — once per card, not once beside
	// the headline and again as the bar's "N / N stages" count.
	await expect(page.getByText('Validating', { exact: true })).toHaveCount(4);
	await expect(page.getByText(/Stage \d+ of \d+/)).toHaveCount(0);
	expect(workItemRequests).toBe(0);

	const movieCard = page.locator('article.activity-row').filter({
		has: page.getByText('Batch 7F3A · Group 1 of 4', { exact: true })
	});
	const rosterButton = movieCard.getByRole('button', { name: 'Posters in This Group' });
	await expect(rosterButton).not.toHaveText(/\d/);
	await rosterButton.click();
	await expect(movieCard.getByText('Arrival', { exact: true })).toBeVisible();
	await expect(movieCard.getByText(/Stage 4 of 9/)).toBeVisible();
	await expect(movieCard.getByText(/3 \/ 8 candidates/)).toBeVisible();
	expect(workItemRequests).toBe(1);
});

test('hides selection until Select is pressed and letters tiles by library', async ({ page }) => {
	await installStableEventSource(page);
	const rows = [
		groupRow('c1000000000000000000000000000001', 'Get Film Posters · 2 Subjects', 'movies', 0, 4),
		groupRow('c2000000000000000000000000000002', 'Get Television Posters · 2 Subjects', 'tv', 1, 4)
	];
	await page.route('**/api/jobs?*', (route) =>
		route.fulfill({ json: { view: 'queue', items: rows, next_cursor: null, limit: 50 } })
	);
	await page.route(/\/api\/jobs\/[^/]+\/contained-work(?:\?.*)?$/, (route) =>
		route.fulfill({ json: containedWorkPage(rows[0].job_id) })
	);

	await page.goto('/projection-room');
	await expect(page.locator('article.activity-row')).toHaveCount(2);

	// "FP" / "TVP" — the tile used to initial these titles into a bare "M" and "T".
	await expect(page.locator('.artwork', { hasText: 'FP' })).toHaveCount(1);
	await expect(page.locator('.artwork', { hasText: 'TVP' })).toHaveCount(1);

	await expect(page.getByRole('checkbox')).toHaveCount(0);
	await page.getByRole('button', { name: 'Select', exact: true }).click();
	await expect(
		page.getByRole('checkbox', { name: 'Select Get Television Posters · 2 Subjects' })
	).toBeVisible();

	await page.getByRole('checkbox', { name: 'Select Get Television Posters · 2 Subjects' }).check();
	await expect(page.getByText('1 selected')).toBeVisible();

	// Leaving the mode drops the selection with it — a checked box nobody can see
	// would keep the bulk bar armed.
	await page.getByRole('button', { name: 'Done', exact: true }).click();
	await expect(page.getByRole('checkbox')).toHaveCount(0);
	await expect(page.getByText('1 selected')).toHaveCount(0);
});

test('keeps unified poster cards and their progress inside a phone viewport', async ({ page }) => {
	await installStableEventSource(page);
	const row = groupRow(
		'b1000000000000000000000000000001',
		'Get Television Posters · 2 Subjects',
		'tv',
		0,
		null,
		'all_at_once'
	);
	await page.route('**/api/jobs?*', (route) =>
		route.fulfill({ json: { view: 'queue', items: [row], next_cursor: null, limit: 50 } })
	);
	await page.route(/\/api\/jobs\/[^/]+\/contained-work(?:\?.*)?$/, (route) =>
		route.fulfill({ json: containedWorkPage(row.job_id) })
	);
	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto('/projection-room');
	const card = page.locator('article.activity-row');
	await expect(card.getByText('Unified run', { exact: true })).toBeVisible();
	await expect(card.getByText(/Batch 7F3A|Group \d/)).toHaveCount(0);
	await card.getByRole('button', { name: 'Posters in This Group' }).click();
	await expect(card.getByText('Arrival', { exact: true })).toBeVisible();
	const bounds = await card.boundingBox();
	expect(bounds).not.toBeNull();
	expect(bounds!.x).toBeGreaterThanOrEqual(0);
	expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
});
