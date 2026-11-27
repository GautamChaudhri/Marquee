import { expect, test } from './fixtures';
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
	// On the host the paths are this machine's: no note about containers.
	await expect(page.getByTestId('paths-note')).toHaveCount(0);
	await expect(settings).not.toContainText('in the containers');
	await expect(page.getByTestId('library')).toContainText('library 58, marquee 1');
	// A blind evaluation's runs are counted on their own line, never among the runs.
	await expect(page.getByTestId('masked-runs')).toHaveText('completed 147, failed 1');
	await expect(page.getByTestId('models').locator('tbody tr')).toHaveCount(3);
	await expect(page.getByTestId('storage')).toContainText('706.7 MB');
	await expect(page.getByRole('link', { name: 'Job 31' })).toHaveAttribute(
		'href',
		'/activity?job=31'
	);
	// The database dump and retention, in the installation and settings tables.
	await expect(page.getByTestId('backup')).toContainText('(2.9 MB); 3 dumps, 8.6 MB in all');
	await expect(page.getByRole('link', { name: 'Job 40' })).toHaveAttribute(
		'href',
		'/activity?job=40'
	);
	await expect(page.getByTestId('retention')).toContainText('deleted 12 items, 4.0 MB');
	await expect(settings).toContainText('Every 24 h; the newest 14 kept');
	await expect(settings).toContainText('finished jobs and unneeded runs after 90 days');
	// No raw JSON anywhere, and nothing on the page changes a setting.
	await expect(page.locator('main')).not.toContainText('{');
	await expect(page.locator('main').getByRole('button')).toHaveCount(0);
	await expect(page.locator('main select, main input')).toHaveCount(0);
});

test('System marks a schema behind, a missing model, a stopped worker, an overdue dump, and failed maintenance in words', async ({
	page
}) => {
	await resetBackend({ problems: true });
	await signIn(page);
	await page.goto('/system');
	const problems = page.getByTestId('problems').locator('li');
	await expect(problems).toHaveCount(6);
	await expect(problems.nth(0)).toHaveText(
		'The database schema is at 0004, and this code needs 0005. Run “marquee db upgrade”.'
	);
	await expect(problems.nth(1)).toContainText('models/dinov2-vits14.onnx is missing');
	await expect(problems.nth(2)).toContainText('No worker is running');
	await expect(problems.nth(3)).toContainText(
		'older than two intervals (48 h). Is the backup job failing?'
	);
	await expect(problems.nth(4)).toHaveText(
		'The last database backup (job 42) failed. Its error is on the Activity page.'
	);
	await expect(problems.nth(5)).toHaveText(
		'The last retention pass (job 43) failed. Its error is on the Activity page.'
	);
	await expect(page.getByTestId('backup')).toHaveClass(/bad/);
	await expect(page.getByTestId('schema')).toContainText('behind: this code needs 0005');
	await expect(page.getByTestId('models')).toContainText('Missing');
	await expect(page.getByTestId('worker')).toContainText('Not running');
});

test('System says the paths are the containers’ in the Compose stack', async ({ page }) => {
	await resetBackend({ production: true });
	await signIn(page);
	await page.goto('/system');
	const settings = page.getByTestId('settings');
	await expect(settings).toContainText('Movie folders (in the containers)');
	await expect(settings).toContainText('Data directory (in the containers)');
	await expect(settings).toContainText('/media/movies/4K');
	await expect(page.getByTestId('paths-note')).toHaveText(
		'Paths inside Marquee’s containers. On the host they are the folders named in the settings file (.env): MOVIE_LIBRARY_HOST_PATH for the movies, MARQUEE_DATA_HOST_PATH for the data.'
	);
});
