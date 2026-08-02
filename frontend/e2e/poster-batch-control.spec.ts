import { expect, test, type Page } from '@playwright/test';

const trigger = (page: Page) => page.getByTestId('batch-trigger');
const control = (page: Page) => page.locator('[aria-label="Poster batch processing"]');

function stubBatchEndpoints(page: Page) {
	return Promise.all([
		page.route('**/api/pipeline/batch', (route) =>
			route.fulfill({
				json: {
					job_id: 'moviebatch0000000000000000000001',
					disposition: 'created',
					phase: 'queued',
					snapshot_url: '/api/jobs/moviebatch0000000000000000000001/snapshot',
					detail_url: '/projection-room/jobs/moviebatch0000000000000000000001'
				}
			})
		),
		page.route('**/api/pipeline/tv/batch', (route) =>
			route.fulfill({
				json: {
					job_id: 'tvbatch000000000000000000000004',
					disposition: 'created',
					phase: 'queued',
					snapshot_url: '/api/jobs/tvbatch000000000000000000000004/snapshot',
					detail_url: '/projection-room/jobs/tvbatch000000000000000000000004'
				}
			})
		)
	]);
}

test('movie and TV Run pages expose per-run batching controls', async ({ page }) => {
	await stubBatchEndpoints(page);
	await page.goto('/pipeline/movies');

	// The trigger reports the current setting without being opened.
	await expect(trigger(page)).toHaveText(/Batching · Chunks of 8/);
	await expect(control(page)).toBeHidden();

	await trigger(page).click();
	const movieControl = control(page);
	await expect(movieControl).toBeVisible();
	await expect(movieControl.locator('.mode-switch button')).toHaveText(['Unified', 'Chunks']);
	await expect(movieControl.getByRole('button', { name: 'Chunks' })).toHaveAttribute(
		'aria-pressed',
		'true'
	);
	const movieChunkSize = movieControl.getByRole('spinbutton', { name: 'Chunk size' });
	await expect(movieChunkSize).toHaveValue('8');
	await expect(movieControl).toContainText('Partially cancel individual chunks.');
	await expect(movieControl).toContainText('Slightly longer processing times.');
	const modeSwitchBox = await movieControl.locator('.mode-switch').boundingBox();
	const stepperBox = await movieControl.locator('.chunk-stepper').boundingBox();
	expect(modeSwitchBox).not.toBeNull();
	expect(stepperBox).not.toBeNull();
	expect(stepperBox!.x).toBeGreaterThanOrEqual(modeSwitchBox!.x + modeSwitchBox!.width);
	await movieControl.getByRole('button', { name: 'Increase chunk size' }).click();
	await expect(movieChunkSize).toHaveValue('9');

	await movieChunkSize.fill('12');
	await movieChunkSize.blur();
	await expect(trigger(page)).toHaveText(/Batching · Chunks of 12/);

	const movieRequest = page.waitForRequest(
		(request) =>
			request.method() === 'POST' && new URL(request.url()).pathname === '/api/pipeline/batch'
	);
	// One click both light-dismisses the popover and fires the button underneath.
	await page.getByRole('button', { name: 'Re-run whole library' }).click();
	expect((await movieRequest).postDataJSON()).toMatchObject({
		scope: 'all',
		batch_mode: 'chunked',
		chunk_size: 12
	});

	// Batching is one standing preference, not a per-library one: the size chosen on
	// Films is the size Television runs with.
	await page.goto('/pipeline/tv');
	await expect(trigger(page)).toHaveText(/Batching · Chunks of 12/);

	await trigger(page).click();
	const tvControl = control(page);
	await expect(tvControl.getByRole('button', { name: 'Increase chunk size' })).toBeVisible();
	await tvControl.getByRole('button', { name: 'Unified' }).click();
	await expect(tvControl.getByRole('spinbutton', { name: 'Chunk size' })).toHaveCount(0);
	await expect(tvControl).toContainText('Fastest overall processing time.');
	await expect(tvControl).toContainText('Cancelling stops everything.');
	await expect(trigger(page)).toHaveText(/Batching · Unified/);

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

test('batching mode survives a reload and follows you between libraries', async ({ page }) => {
	await stubBatchEndpoints(page);
	await page.goto('/pipeline/tv');

	await trigger(page).click();
	await control(page).getByRole('button', { name: 'Unified' }).click();
	await expect(trigger(page)).toHaveText(/Batching · Unified/);

	await page.reload();
	await expect(trigger(page)).toHaveText(/Batching · Unified/);

	await page.goto('/pipeline/movies');
	await expect(trigger(page)).toHaveText(/Batching · Unified/);
});

test('Escape closes the batching popover', async ({ page }) => {
	await stubBatchEndpoints(page);
	await page.goto('/pipeline/movies');

	await trigger(page).click();
	await expect(control(page)).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(control(page)).toBeHidden();
});
