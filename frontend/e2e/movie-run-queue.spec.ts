import { expect, test, type Page } from '@playwright/test';

const jobId = 'filmrunqueued00000000000000000001';
const now = '2026-08-01T12:00:00Z';

const movies = [
	{
		id: 8101,
		title: 'Synthetic Feature One',
		year: 2019,
		tmdb_id: 8101,
		genres: null,
		container: null,
		video_width: null,
		video_height: null,
		resolution: null,
		poster_status: 'missing',
		poster_url: null,
		media_file_id: null
	},
	{
		id: 8102,
		title: 'Synthetic Feature Two',
		year: 2021,
		tmdb_id: 8102,
		genres: null,
		container: null,
		video_width: null,
		video_height: null,
		resolution: null,
		poster_status: 'missing',
		poster_url: null,
		media_file_id: null
	}
];

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

function terminalSnapshot() {
	return {
		version: 1,
		job_id: jobId,
		type: 'poster_pipeline',
		label: 'Select the best poster',
		phase: 'terminal',
		outcome: 'succeeded',
		desired_state: 'run',
		fence_token: 1,
		execution_class: 'gpu',
		progress_sequence: 2,
		progress: null,
		status: {
			label: 'Completed',
			label_key: 'jobs.status.succeeded',
			phase: 'terminal',
			outcome: 'succeeded',
			tone: 'positive'
		},
		attention: { level: 'normal', reason: 'none', message: null, remediation: null },
		priority: 0,
		allowed_actions: [],
		links: {
			detail: `/projection-room/jobs/${jobId}`,
			presentation: `/api/jobs/${jobId}/presentation`,
			snapshot: `/api/jobs/${jobId}/snapshot`
		},
		configuration_version: 1,
		eligible_at: null,
		created_at: now,
		started_at: now,
		terminal_at: now,
		parent_id: null,
		root_id: jobId,
		retry_of_job_id: null,
		updated_at: now,
		last_event_id: 2
	};
}

function activeMovieJobRow() {
	return {
		version: 1,
		job_id: 'movieactivity00000000000000000001',
		job_type: 'poster_pipeline',
		label: 'Synthetic Feature One',
		label_key: 'jobs.poster_pipeline.label',
		feature_area: 'ai_posters',
		feature_label: 'Posters',
		presentation_family: 'ai_posters',
		subject: {
			kind: 'movie',
			display_id: 'movie:8101',
			display_name: 'Synthetic Feature One',
			artwork_key: null,
			context: ['2019', 'Movie'],
			snapshot_at: now,
			missing_live_subject: false,
			monogram: null
		},
		action_headline: 'Select the best poster',
		status: {
			label: 'Running',
			label_key: 'jobs.status.running',
			phase: 'running',
			outcome: null,
			tone: 'active'
		},
		attention: { level: 'normal', reason: 'none', message: null, remediation: null },
		trigger: { kind: 'manual', label: 'Started manually', initiator: 'Operator' },
		progress: {
			sequence: 1,
			headline: 'Validating',
			stage_key: 'validating',
			stage_label: 'Validating',
			freshness: 'live',
			updated_at: now,
			overall: {
				scope_id: 'movie:8101',
				mode: 'determinate',
				label: 'Stages',
				percent: 40,
				completed: 4,
				total: 10,
				unit: 'stages'
			},
			current: null,
			current_subject: null,
			wait: null
		},
		work_items: null,
		contained_work: null,
		priority: 0,
		fence_token: 1,
		execution_class: 'gpu',
		queue_rank: 1,
		is_parent: false,
		allowed_actions: ['cancel', 'open_detail'],
		links: {
			detail: '/projection-room/jobs/movieactivity00000000000000000001',
			presentation: '/api/jobs/movieactivity00000000000000000001/presentation',
			snapshot: '/api/jobs/movieactivity00000000000000000001/snapshot'
		},
		parent_id: null,
		root_id: 'movieactivity00000000000000000001',
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

test('individual Film Run stays in the workspace and refreshes both queues when it settles', async ({
	page
}) => {
	await installStableEventSource(page);
	let runItems = [...movies];
	let reviewItems: object[] = [];

	await page.route('**/api/pipeline/run-queue*', (route) =>
		route.fulfill({
			json: { total: runItems.length, page: 1, page_size: 60, items: runItems }
		})
	);
	await page.route('**/api/pipeline/review-queue*', (route) =>
		route.fulfill({
			json: { total: reviewItems.length, page: 1, page_size: 60, items: reviewItems }
		})
	);
	await page.route('**/api/pipeline/movie/8101/run', (route) => {
		runItems = [movies[1]];
		return route.fulfill({
			json: {
				job_id: jobId,
				disposition: 'created',
				phase: 'queued',
				snapshot_url: `/api/jobs/${jobId}/snapshot`,
				detail_url: `/projection-room/jobs/${jobId}`
			}
		});
	});
	await page.route(`**/api/jobs/${jobId}/snapshot`, (route) => {
		reviewItems = [
			{
				movie: { ...movies[0], poster_url: '/stale-movie-poster.svg' },
				run: {
					run_id: 'synthetic-review-run-8101',
					counts: { ranked: 4, total_candidates: 8 },
					scorer_name: 'synthetic',
					started_at: now
				},
				auto_pick_poster_url: null
			}
		];
		return route.fulfill({ json: terminalSnapshot() });
	});
	await page.route('**/stale-movie-poster.svg', (route) =>
		route.fulfill({
			contentType: 'image/svg+xml',
			body: '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1" />'
		})
	);

	await page.goto('/pipeline/movies');
	await expect(page.getByRole('tab', { name: /Run\s*2/ })).toBeVisible();

	await page
		.locator('.run-card')
		.filter({ hasText: 'Synthetic Feature One' })
		.locator('.run-poster-wrap')
		.hover();
	await page.getByRole('button', { name: 'Run poster pipeline for Synthetic Feature One' }).click();

	await expect(page.locator('.run-card').filter({ hasText: 'Synthetic Feature One' })).toHaveCount(
		0
	);
	await expect(page.getByRole('tab', { name: /Run\s*1/ })).toBeVisible();
	expect(new URL(page.url()).pathname).toBe('/pipeline/movies');
	await expect(page.getByRole('tab', { name: /Review\s*1/ })).toBeVisible();
	await expect(page.getByRole('tab', { name: /Run\s*1/ })).toHaveAttribute('aria-selected', 'true');

	await page.getByRole('tab', { name: /Review\s*1/ }).click();
	const reviewCard = page.locator('.rev-card').filter({ hasText: 'Synthetic Feature One' });
	await expect(reviewCard).toBeVisible();
	await expect(reviewCard).not.toContainText('Awaiting your choice');
	const statsRow = reviewCard.locator('.rev-stats-row');
	await expect(statsRow.locator('.rev-stats')).toHaveText('4 candidates');
	await expect(statsRow.locator('.rev-age')).toHaveText(/just now|\d+[mhd] ago|—/);
	await expect(reviewCard.locator('.rev-stats')).toHaveClass(/warn/);
	await expect(reviewCard.locator('.rev-poster img')).toHaveCount(0);
	await expect(reviewCard.locator('.rev-poster .dot')).toHaveCount(0);
});

test('movie activity has visible separation from the run grid', async ({ page }) => {
	await installStableEventSource(page);
	await page.route('**/api/jobs?*', (route) =>
		route.fulfill({
			json: { view: 'queue', items: [activeMovieJobRow()], next_cursor: null, limit: 50 }
		})
	);

	await page.goto('/pipeline/movies');
	const panel = page.locator('section[aria-label="Movie poster activity"]');
	const grid = page.locator('.rev-grid');
	await expect(panel).toBeVisible();
	await expect(grid).toBeVisible();

	const panelBox = await panel.boundingBox();
	const gridBox = await grid.boundingBox();
	expect(panelBox).not.toBeNull();
	expect(gridBox).not.toBeNull();
	expect(gridBox!.y).toBeGreaterThanOrEqual(panelBox!.y + panelBox!.height + 12);
});
