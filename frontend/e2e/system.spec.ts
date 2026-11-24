import { expect, test } from '@playwright/test';
import { resetBackend, signIn } from './helpers';

test('System shows the installation in plain tables, and nothing is wrong', async ({ page }) => {
	await resetBackend();
	await signIn(page);
	await page.goto('/system');
	await expect(page.getByTestId('no-problems')).toHaveText('Nothing needs attention.');
	await expect(page.getByTestId('schema')).toContainText('0005 (the newest)');
	await expect(page.getByTestId('worker')).toContainText('Running since');
	const settings = page.getByTestId('settings');
	await expect(settings).toContainText('Off: a run’s pick is written only when you choose it');
	await expect(settings).toContainText('Every 6 h');
	await expect(settings).toContainText('/srv/media/movies/4K');
	await expect(settings).toContainText('/srv/marquee/data');
	await expect(page.getByTestId('library')).toContainText('library 58, marquee 1');
	// A blind evaluation's runs are counted on their own line, never among the runs.
	await expect(page.getByTestId('masked-runs')).toHaveText('completed 147, failed 1');
	await expect(page.getByTestId('models').locator('tbody tr')).toHaveCount(3);
	await expect(page.getByTestId('storage')).toContainText('706.7 MB');
	await expect(page.getByRole('link', { name: 'Job 31' })).toHaveAttribute(
		'href',
		'/activity?job=31'
	);
	// No raw JSON anywhere, and nothing on the page changes a setting.
	await expect(page.locator('main')).not.toContainText('{');
	await expect(page.locator('main').getByRole('button')).toHaveCount(0);
	await expect(page.locator('main select, main input')).toHaveCount(0);
});

test('System marks a schema behind, a missing model, and a stopped worker in words', async ({
	page
}) => {
	await resetBackend({ problems: true });
	await signIn(page);
	await page.goto('/system');
	const problems = page.getByTestId('problems').locator('li');
	await expect(problems).toHaveCount(3);
	await expect(problems.nth(0)).toHaveText(
		'The database schema is at 0004, and this code needs 0005. Run “marquee db upgrade”.'
	);
	await expect(problems.nth(1)).toContainText('models/dinov2-vits14.onnx is missing');
	await expect(problems.nth(2)).toContainText('No worker is running');
	await expect(page.getByTestId('schema')).toContainText('behind: this code needs 0005');
	await expect(page.getByTestId('models')).toContainText('Missing');
	await expect(page.getByTestId('worker')).toContainText('Not running');
});
