import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { installTasteFixtures } from './support/taste-fixtures';

const fixedTime = new Date('2026-08-02T12:05:00Z');

async function stablePage(
	page: Page,
	viewport = { width: 1440, height: 1000 },
	colorScheme: 'dark' | 'light' = 'dark'
) {
	await page.setViewportSize(viewport);
	await page.clock.setFixedTime(fixedTime);
	await page.emulateMedia({ colorScheme, reducedMotion: 'reduce' });
	await page.addInitScript((theme) => {
		localStorage.setItem('marquee:theme', JSON.stringify(theme));
		class StableEventSource {
			addEventListener(type: string, listener: (event: Event) => void) {
				if (type === 'open') queueMicrotask(() => listener(new Event('open')));
			}
			close() {}
		}
		Object.defineProperty(window, 'EventSource', { value: StableEventSource });
	}, colorScheme);
}

async function certify(page: Page, name: string) {
	await page.evaluate(() => document.fonts.ready);
	await expect(page.locator('main')).toBeVisible();
	expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
	await expect(page.locator('main')).toHaveScreenshot(name);
}

function activeFilmRow() {
	const now = fixedTime.toISOString();
	return {
		version: 1,
		job_id: 'visualfilmactivity000000000000001',
		job_type: 'poster_pipeline_group',
		label: 'Get Film Posters · 8 Subjects',
		label_key: 'jobs.poster_pipeline_group.label',
		feature_area: 'ai_posters',
		feature_label: 'Posters',
		presentation_family: 'ai_posters',
		subject: {
			kind: 'poster_subject_group',
			display_id: 'poster-group:movies:1',
			display_name: 'Get Film Posters · 8 Subjects',
			artwork_key: null,
			context: ['Unified run'],
			snapshot_at: now,
			missing_live_subject: false,
			monogram: 'FP'
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
			headline: 'Filtering',
			stage_key: 'filtering',
			stage_label: 'Filtering',
			freshness: 'live',
			updated_at: now,
			overall: {
				scope_id: 'poster-group:movies:1',
				mode: 'determinate',
				label: 'Stages',
				percent: 63.6,
				completed: 7,
				total: 11,
				unit: 'stages'
			},
			current: {
				scope_id: 'movie:8101',
				mode: 'determinate',
				label: 'Current work',
				percent: 11.4,
				completed: 76,
				total: 666,
				unit: 'candidates'
			},
			current_subject: null,
			wait: null
		},
		work_items: null,
		contained_work: {
			version: 1,
			source: 'work_items',
			label: 'Posters in this run',
			item_label_singular: 'subject',
			item_label_plural: 'subjects',
			total: 8,
			completed: 2,
			counts: {
				pending: 5,
				running: 1,
				retrying: 0,
				succeeded: 2,
				no_change: 0,
				review_required: 0,
				failed: 0,
				cancelled: 0
			},
			sequence: 7,
			updated_at: now,
			href: '/api/jobs/visualfilmactivity000000000000001/contained-work'
		},
		priority: 50,
		fence_token: 3,
		execution_class: 'gpu',
		queue_rank: 1,
		is_parent: false,
		allowed_actions: ['cancel', 'open_detail'],
		links: {
			detail: '/projection-room/jobs/visualfilmactivity000000000000001',
			presentation: '/api/jobs/visualfilmactivity000000000000001/presentation',
			snapshot: '/api/jobs/visualfilmactivity000000000000001/snapshot'
		},
		parent_id: null,
		root_id: 'visualfilmactivity000000000000001',
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

const film = (id: number, title: string, year: number) => ({
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

test('Film run workspace activity and grid', async ({ page }) => {
	await stablePage(page);
	await page.route('**/api/jobs?*', (route) =>
		route.fulfill({
			json: { view: 'queue', items: [activeFilmRow()], next_cursor: null, limit: 20 }
		})
	);
	await page.goto('/pipeline/movies');
	await expect(page.getByRole('heading', { name: 'Movie poster activity' })).toBeVisible();
	await certify(page, 'film-run-activity-desktop.png');
});

test('Film review manual, personalized, and no-survivor cards', async ({ page }) => {
	await stablePage(page);
	const reviewItems = [
		{
			movie: film(8501, 'Manual Review Film', 2021),
			run: {
				run_id: 'visual-manual',
				status: 'completed',
				started_at: fixedTime.toISOString(),
				completed_at: fixedTime.toISOString(),
				scorer_name: null,
				counts: { ranked: 6 },
				reviewed: false
			},
			auto_pick_poster_url: null,
			results_url: '/api/pipeline/runs/visual-manual'
		},
		{
			movie: film(8502, 'Personalized Film', 2022),
			run: {
				run_id: 'visual-personalized',
				status: 'completed',
				started_at: fixedTime.toISOString(),
				completed_at: fixedTime.toISOString(),
				scorer_name: 'weighted',
				counts: { ranked: 12 },
				reviewed: false
			},
			auto_pick_poster_url: '/visual-auto-pick.svg',
			results_url: '/api/pipeline/runs/visual-personalized'
		},
		{
			movie: film(8503, 'No Survivor Film', 2023),
			run: {
				run_id: 'visual-empty',
				status: 'flagged_manual',
				started_at: fixedTime.toISOString(),
				completed_at: fixedTime.toISOString(),
				scorer_name: null,
				counts: { ranked: 0 },
				reviewed: false
			},
			auto_pick_poster_url: null,
			results_url: '/api/pipeline/runs/visual-empty'
		}
	];
	await page.route('**/visual-auto-pick.svg', (route) =>
		route.fulfill({
			contentType: 'image/svg+xml',
			body: '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="900"><rect width="100%" height="100%" fill="#17243d"/><circle cx="300" cy="400" r="150" fill="none" stroke="#fbbf24" stroke-width="12"/><text x="300" y="760" text-anchor="middle" fill="#fbbf24" font-size="42">TOP PICK</text></svg>'
		})
	);
	await page.route('**/api/pipeline/review-queue*', (route) =>
		route.fulfill({ json: { total: 3, page: 1, page_size: 60, items: reviewItems } })
	);
	await page.route('**/api/pipeline/run-queue*', (route) =>
		route.fulfill({ json: { total: 0, page: 1, page_size: 60, items: [] } })
	);
	await page.goto('/pipeline/movies?tab=review');
	await expect(page.locator('.rev-card')).toHaveCount(3);
	await certify(page, 'film-review-states-desktop.png');
});

test('Television library grid and table composition', async ({ page }) => {
	await stablePage(page);
	await page.goto('/television');
	await expect(page.getByRole('button', { name: 'Grid view' })).toHaveAttribute(
		'aria-pressed',
		'true'
	);
	await certify(page, 'television-library-grid-desktop.png');
	await page.getByRole('button', { name: 'Table view' }).click();
	await expect(
		page.getByRole('table', { name: 'Television series with poster previews and genres' })
	).toBeVisible();
	await certify(page, 'television-library-table-desktop.png');
});

for (const library of ['movies', 'tv'] as const) {
	test(`Key Art Engine ${library} profile desktop`, async ({ page }) => {
		await stablePage(page);
		await installTasteFixtures(page);
		await page.goto(`/taste?library=${library}`);
		await page.getByRole('button', { name: 'Hide map' }).click();
		await expect(
			page.getByText(`${library === 'movies' ? 'Movies' : 'TV'} profile · generation`).first()
		).toBeVisible();
		await certify(page, `key-art-${library}-desktop.png`);
	});

	test(`Key Art Engine ${library} profile mobile`, async ({ page }) => {
		await stablePage(page, { width: 390, height: 844 });
		await installTasteFixtures(page);
		await page.goto(`/taste?library=${library}`);
		await page.getByRole('button', { name: 'Hide map' }).click();
		await expect(
			page.getByText(`${library === 'movies' ? 'Movies' : 'TV'} profile · generation`).first()
		).toBeVisible();
		await certify(page, `key-art-${library}-mobile.png`);
	});
}

for (const colorScheme of ['dark', 'light'] as const) {
	for (const viewport of [
		{ label: 'desktop', size: { width: 1440, height: 1000 } },
		{ label: 'mobile', size: { width: 390, height: 844 } }
	] as const) {
		test(`Settings Posters advanced ${colorScheme} ${viewport.label}`, async ({ page }) => {
			await stablePage(page, viewport.size, colorScheme);
			await page.goto('/settings?tab=posters&level=advanced');
			await expect(page.getByRole('tab', { name: 'Posters' })).toHaveAttribute(
				'aria-selected',
				'true'
			);
			await expect(page.getByRole('button', { name: 'Advanced' })).toHaveAttribute(
				'aria-pressed',
				'true'
			);
			await certify(page, `settings-posters-advanced-${colorScheme}-${viewport.label}.png`);
		});
	}
}
