import { expect, test } from '@playwright/test';
import { APP_URL } from '../playwright.config';
import { resetBackend, signIn } from './helpers';

test.beforeEach(async ({ page }) => {
	await resetBackend();
	await signIn(page);
});

test('Activity shows the worker and the jobs, newest first, and filters them', async ({ page }) => {
	await page.goto('/activity');
	await expect(page.getByTestId('worker-panel')).toContainText('The worker is running');
	const rows = page.getByTestId('job-row');
	await expect(rows).toHaveCount(3);
	await expect(rows.first()).toContainText('Run');
	await expect(rows.first()).toContainText('queued');
	// A job about a movie names it, linked to the movie's page; a sync names none.
	await expect(rows.first().getByRole('link', { name: 'Arrival (2016)' })).toHaveAttribute(
		'href',
		'/movies/3'
	);
	await expect(rows.nth(1).getByRole('link', { name: /\(\d{4}\)/ })).toHaveCount(0);

	await page.getByLabel('State').selectOption('failed');
	await expect(rows).toHaveCount(1);
	await expect(rows.first()).toContainText('Radarr answered 503');
	await page.getByLabel('State').selectOption('');
	await page.getByLabel('Kind').selectOption('library_sync');
	await expect(rows).toHaveCount(1);
});

test('Activity updates by itself as a job moves on', async ({ page }) => {
	await page.goto('/activity');
	await expect(page.getByTestId('job-row')).toHaveCount(3);
	// Something else queues a sync; the page is not reloaded.
	const queued = await page.request.post('/api/sync', { headers: { origin: APP_URL } });
	expect(queued.status()).toBe(202);
	const id = (await queued.json()).id as number;
	const row = page.getByTestId('job-row').filter({ hasText: `#${id}` });
	await expect(row).toBeVisible({ timeout: 10_000 });
	await expect(row).toContainText('succeeded', { timeout: 10_000 });
});

test('a job’s detail shows its events; Cancel and Retry work where allowed', async ({ page }) => {
	await page.goto('/activity');
	const rows = page.getByTestId('job-row');
	await rows
		.filter({ hasText: 'queued' })
		.getByRole('link', { name: /^#\d+$/ })
		.click();
	const detail = page.getByTestId('job-detail');
	await expect(detail.getByTestId('job-events')).toContainText('queued');
	await expect(detail.getByTestId('job-movie')).toHaveText('Arrival (2016)');
	await expect(detail.getByTestId('job-movie')).toHaveAttribute('href', '/movies/3');
	await detail.getByRole('button', { name: 'Cancel' }).click();
	await expect(detail).toContainText('cancelled');
	await expect(detail.getByRole('button', { name: 'Cancel' })).toHaveCount(0);

	await page.getByRole('button', { name: 'Close the job' }).click();
	await rows
		.filter({ hasText: 'failed' })
		.getByRole('link', { name: /^#\d+$/ })
		.click();
	await expect(detail).toContainText('Radarr answered 503');
	await detail.getByRole('button', { name: 'Retry' }).click();
	await expect(detail.getByTestId('job-events')).toContainText('retried');
	await expect(detail).toContainText('succeeded', { timeout: 10_000 });
});
