import { expect, test, type Page } from '@playwright/test';

const now = '2026-08-02T12:00:00Z';

const movie = (id: number, title: string, year: number) => ({
	id,
	title,
	year,
	tmdb_id: id,
	genres: null,
	container: null,
	video_width: null,
	video_height: null,
	resolution: null,
	poster_status: 'missing',
	review_pending: true,
	poster_url: null,
	media_file_id: id
});

function movieReviewItem(
	id: number,
	title: string,
	year: number,
	ranked: number,
	autoPick: string | null
) {
	return {
		movie: movie(id, title, year),
		run: {
			run_id: `review-run-${id}`,
			status: ranked === 0 ? 'flagged_manual' : 'completed',
			started_at: now,
			completed_at: now,
			scorer_name: autoPick ? 'weighted' : null,
			counts: { ranked, total_candidates: ranked },
			reviewed: false
		},
		auto_pick_poster_url: autoPick,
		results_url: `/api/pipeline/runs/review-run-${id}`
	};
}

async function svgPoster(page: Page) {
	await page.route('**/fixture-auto-pick.svg', (route) =>
		route.fulfill({
			contentType: 'image/svg+xml',
			body: '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="900"><rect width="100%" height="100%" fill="#17243d"/><text x="300" y="470" text-anchor="middle" fill="#fbbf24" font-size="42">AUTO PICK</text></svg>'
		})
	);
}

test('Film review reset is durable, immediate, and keeps cold-start states honest', async ({
	page
}) => {
	await page.clock.setFixedTime(new Date('2026-08-02T12:05:00Z'));
	await svgPoster(page);
	let reviewItems = [
		movieReviewItem(8401, 'Manual Review Film', 2021, 6, null),
		movieReviewItem(8402, 'Personalized Film', 2022, 12, '/fixture-auto-pick.svg'),
		movieReviewItem(8403, 'No Survivor Film', 2023, 0, null)
	];
	let runItems: ReturnType<typeof movie>[] = [];
	let resetRequests = 0;

	await page.route('**/api/pipeline/review-queue*', (route) =>
		route.fulfill({
			json: { total: reviewItems.length, page: 1, page_size: 60, items: reviewItems }
		})
	);
	await page.route('**/api/pipeline/run-queue*', (route) =>
		route.fulfill({
			json: { total: runItems.length, page: 1, page_size: 60, items: runItems }
		})
	);
	await page.route('**/api/pipeline/review-queue/reset', (route) => {
		resetRequests += 1;
		expect(route.request().method()).toBe('POST');
		expect(route.request().headers()['idempotency-key']).toMatch(/^poster_deploy_reset:/);
		runItems = reviewItems.map((item) => ({ ...item.movie, review_pending: false }));
		reviewItems = [];
		return route.fulfill({
			status: 202,
			json: {
				job_id: 'filmreset000000000000000000000001',
				disposition: 'created',
				idempotent: false,
				phase: 'queued',
				snapshot_url: '/api/jobs/filmreset000000000000000000000001/snapshot',
				detail_url: '/projection-room/jobs/filmreset000000000000000000000001',
				activity_url: '/projection-room?view=queue&job=filmreset000000000000000000000001',
				active_conflict: null
			}
		});
	});

	await page.goto('/pipeline/movies?tab=review');
	const manual = page.locator('.rev-card').filter({ hasText: 'Manual Review Film' });
	const personalized = page.locator('.rev-card').filter({ hasText: 'Personalized Film' });
	const empty = page.locator('.rev-card').filter({ hasText: 'No Survivor Film' });
	await expect(manual.locator('.rev-stats')).toHaveText('6 candidates');
	await expect(manual.locator('.rev-stats')).toHaveClass(/warn/);
	await expect(manual.locator('.rev-poster img')).toHaveCount(0);
	await expect(personalized.locator('.rev-stats')).toHaveClass(/good/);
	await expect(personalized.locator('.rev-poster img')).toHaveAttribute(
		'src',
		'/fixture-auto-pick.svg'
	);
	await expect(empty.locator('.rev-stats')).toHaveText('0 candidates');
	await expect(empty.locator('.rev-stats')).toHaveClass(/bad/);
	await expect(page.getByText('Awaiting your choice')).toHaveCount(0);

	await page.getByRole('button', { name: 'Reset all in review' }).click();
	await page
		.getByRole('dialog', { name: 'Reset 3 movies?' })
		.getByRole('button', { name: 'Reset 3 movies' })
		.click();

	await expect(page.getByRole('tab', { name: /Run\s*3/ })).toHaveAttribute('aria-selected', 'true');
	await expect(page.getByRole('tab', { name: /Review\s*0/ })).toBeVisible();
	await expect(page.locator('.run-card')).toHaveCount(3);
	await expect(page.getByRole('heading', { name: 'Movie poster activity' })).toHaveCount(0);
	expect(new URL(page.url()).pathname).toBe('/pipeline/movies');
	expect(resetRequests).toBe(1);
});

test('Television review reset uses the same durable workspace-only flow', async ({ page }) => {
	let reviewItems = [
		{
			series: { id: 9401, title: 'Reset Review Series', year: 2020, tmdb_id: 9401 },
			show_run: {
				run_id: 'tv-reset-review-show',
				status: 'completed',
				started_at: now,
				completed_at: now,
				scorer_name: null,
				counts: { ranked: 7 },
				reviewed: false,
				auto_pick_poster_url: null
			},
			season_runs: [],
			seasons_only: false,
			flagged_no_candidates: false
		}
	];
	let runItems: object[] = [];
	let resetRequests = 0;

	await page.route('**/api/pipeline/tv/review-queue*', (route) =>
		route.fulfill({
			json: { total_series: reviewItems.length, page: 1, page_size: 200, items: reviewItems }
		})
	);
	await page.route('**/api/pipeline/tv/run-queue', (route) =>
		route.fulfill({ json: { total: runItems.length, items: runItems } })
	);
	await page.route('**/api/pipeline/tv/review-queue/reset', (route) => {
		resetRequests += 1;
		expect(route.request().method()).toBe('POST');
		expect(route.request().headers()['idempotency-key']).toMatch(/^poster_deploy_reset:/);
		runItems = [
			{
				series: reviewItems[0].series,
				show_poster_missing: true,
				missing_seasons: [],
				assets_to_run: [{ media_type: 'series' }],
				no_tmdb: false
			}
		];
		reviewItems = [];
		return route.fulfill({
			status: 202,
			json: {
				job_id: 'tvreset0000000000000000000000001',
				disposition: 'created',
				idempotent: false,
				phase: 'queued',
				snapshot_url: '/api/jobs/tvreset0000000000000000000000001/snapshot',
				detail_url: '/projection-room/jobs/tvreset0000000000000000000000001',
				activity_url: '/projection-room?view=queue&job=tvreset0000000000000000000000001',
				active_conflict: null
			}
		});
	});

	await page.goto('/pipeline/tv?tab=review');
	await expect(page.getByRole('tab', { name: /Review\s*1/ })).toHaveAttribute(
		'aria-selected',
		'true'
	);
	await page.getByRole('button', { name: 'Reset all in review' }).click();
	await page
		.getByRole('dialog', { name: 'Reset 1 shows?' })
		.getByRole('button', { name: 'Reset 1 shows' })
		.click();

	await expect(page.getByRole('tab', { name: /Run\s*1/ })).toHaveAttribute('aria-selected', 'true');
	await expect(page.getByRole('tab', { name: /Review\s*0/ })).toBeVisible();
	await expect(page.locator('.run-row').filter({ hasText: 'Reset Review Series' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'TV poster activity' })).toHaveCount(0);
	expect(new URL(page.url()).pathname).toBe('/pipeline/tv');
	expect(resetRequests).toBe(1);
});
