import { expect, test, type Page } from '@playwright/test';

async function expectDefaultChunkControl(page: Page) {
	const control = page.locator('[aria-label="Poster batch processing"]');
	await expect(control).toBeVisible();
	await expect(control.getByRole('button', { name: 'Chunks' })).toHaveAttribute(
		'aria-pressed',
		'true'
	);
	await expect(control.getByRole('spinbutton', { name: 'Chunk size' })).toHaveValue('8');
	return control;
}

test('movie and TV Run pages expose per-run batching controls', async ({ page }) => {
	await page.route('**/api/pipeline/batch', (route) =>
		route.fulfill({
			json: {
				job_id: 'moviebatch0000000000000000000001',
				disposition: 'created',
				phase: 'queued',
				snapshot_url: '/api/jobs/moviebatch0000000000000000000001/snapshot',
				detail_url: '/projection-room/jobs/moviebatch0000000000000000000001'
			}
		})
	);
	await page.route('**/api/pipeline/tv/batch', (route) =>
		route.fulfill({
			json: {
				job_id: 'tvbatch000000000000000000000004',
				disposition: 'created',
				phase: 'queued',
				snapshot_url: '/api/jobs/tvbatch000000000000000000000004/snapshot',
				detail_url: '/projection-room/jobs/tvbatch000000000000000000000004'
			}
		})
	);
	await page.goto('/pipeline/movies');

	const movieControl = await expectDefaultChunkControl(page);
	await movieControl.getByRole('spinbutton', { name: 'Chunk size' }).fill('12');
	await expect(movieControl.getByRole('spinbutton', { name: 'Chunk size' })).toHaveValue('12');
	const movieRequest = page.waitForRequest(
		(request) =>
			request.method() === 'POST' && new URL(request.url()).pathname === '/api/pipeline/batch'
	);
	await page.getByRole('button', { name: 'Re-run whole library' }).click();
	expect((await movieRequest).postDataJSON()).toMatchObject({
		scope: 'all',
		batch_mode: 'chunked',
		chunk_size: 12
	});

	await page.goto('/pipeline/tv');
	const tvControl = await expectDefaultChunkControl(page);
	await tvControl.getByRole('button', { name: 'Unified' }).click();
	await expect(tvControl.getByRole('spinbutton', { name: 'Chunk size' })).toHaveCount(0);
	await expect(tvControl).toContainText('cancelling stops the complete run');
	const tvRequest = page.waitForRequest(
		(request) =>
			request.method() === 'POST' && new URL(request.url()).pathname === '/api/pipeline/tv/batch'
	);
	await page.getByRole('button', { name: 'Run all missing' }).click();
	const tvBody = (await tvRequest).postDataJSON();
	expect(tvBody).toMatchObject({
		scope: 'missing',
		batch_mode: 'all_at_once'
	});
	expect(tvBody).not.toHaveProperty('chunk_size');
});
