import { expect, test } from '@playwright/test';
import { resetBackend, signIn } from './helpers';

test.beforeEach(async ({ page }) => {
	await resetBackend();
	await signIn(page);
});

test('the Library renders the grid, loads more, and filters', async ({ page }) => {
	const tiles = page.getByTestId('movie-tile');
	await expect(tiles).toHaveCount(48);
	await expect(page.getByText('60 movies')).toBeVisible();
	const first = tiles.first();
	await expect(first).toContainText('Alien');
	await expect(first).toContainText('1979');
	await expect(first.locator('img')).toHaveAttribute(
		'src',
		/\/api\/movies\/2\/poster\?width=320&v=[0-9a-f]{12}$/
	);
	await expect(first.getByText('Marquee', { exact: true })).toBeVisible();

	// Moon sorts last, past the first page.
	await page.getByRole('button', { name: /Load more/ }).click();
	await expect(tiles).toHaveCount(60);
	await expect(tiles.filter({ hasText: 'Moon' }).getByText('No poster')).toBeVisible();

	await page.getByLabel('Search titles').fill('arr');
	await expect(page).toHaveURL(/\?q=arr$/);
	await expect(tiles).toHaveCount(1);
	await expect(tiles.first()).toContainText('Arrival');
	await page.getByLabel('Search titles').fill('');
	await expect(tiles).toHaveCount(48);

	await page.getByLabel('Whose').selectOption('marquee');
	await expect(tiles).toHaveCount(1);
	await expect(page.getByText('1 movie match')).toBeVisible();
	await page.getByLabel('Whose').selectOption('any');

	await page.getByLabel('Poster').selectOption('no');
	await expect(tiles).toHaveCount(1);
	await expect(tiles.first()).toContainText('Moon');
	await page.getByLabel('Poster').selectOption('any');

	await page.getByLabel('Runs').selectOption('yes');
	await expect(tiles).toHaveCount(1);
	await expect(tiles.first()).toContainText('Heat');

	await page.getByLabel('Search titles').fill('nothing like this');
	await expect(page.getByText('No movie matches.')).toBeVisible();
	await page.getByRole('button', { name: 'Clear filters' }).click();
	await expect(tiles).toHaveCount(48);
});

test('Sync now says what happened and links to Activity', async ({ page }) => {
	await page.getByRole('button', { name: 'Sync now' }).click();
	const note = page.getByTestId('sync-note');
	await expect(note).toContainText(/Sync queued as job \d+\./);
	await expect(note).toContainText('Done', { timeout: 10_000 });
	await note.getByRole('link', { name: 'Follow it in Activity' }).click();
	await expect(page).toHaveURL(/\/activity\?job=\d+$/);
	await expect(page.getByTestId('job-detail')).toContainText('Library sync');
});

test('a movie page shows its poster, facts, latest run, and history', async ({ page }) => {
	await page.goto('/movies/1');
	await expect(page.getByRole('heading', { name: /Heat/ })).toBeVisible();
	await expect(page.getByText('A Los Angeles crime saga.')).toBeVisible();
	await expect(page.getByAltText('Poster of Heat')).toHaveAttribute('src', /width=640/);
	await expect(page.getByTestId('run-meta')).toContainText('4 ranked of 5 considered');
	const candidates = page.getByTestId('candidates').locator('li');
	await expect(candidates).toHaveCount(4);
	await expect(candidates.first()).toContainText('#1');
	await expect(candidates.first()).toContainText('Pick');
	await expect(page.getByTestId('poster-history').locator('li')).toHaveCount(2);
	// Read-only: nothing on this page deploys or gives feedback.
	await expect(page.getByRole('button', { name: /deploy|approve|pick|reject/i })).toHaveCount(0);
});

test('Run follows its job, and a refusal shows its reason', async ({ page }) => {
	await page.goto('/movies/2');
	await expect(page.getByText('This movie has never been run.')).toBeVisible();
	await page.getByRole('button', { name: 'Run', exact: true }).click();
	await expect(page.getByTestId('run-job')).toContainText('Done', { timeout: 10_000 });
	await expect(page.getByTestId('candidates').locator('li')).toHaveCount(4);

	// A run of this movie is already queued: that job is shown, not a second one.
	await page.goto('/movies/3');
	await page.getByRole('button', { name: 'Run', exact: true }).click();
	await expect(page.getByTestId('run-note')).toHaveText('A run of this movie is already queued.');

	await page.goto('/movies/4');
	await page.getByRole('button', { name: 'Run', exact: true }).click();
	await expect(page.getByTestId('run-note')).toHaveText(
		'Movie 4 (Moon) has no TMDB details yet; sync it first.'
	);
	await expect(page.getByText('No poster').first()).toBeVisible();
});

test('a restore asks first, then follows its job', async ({ page }) => {
	await page.goto('/movies/1');
	const buttons = page.getByRole('button', { name: 'Put back what was there before' });
	await buttons.last().click();
	const dialog = page.getByRole('dialog');
	await expect(dialog).toContainText('your own poster');
	await dialog.getByRole('button', { name: 'Cancel' }).click();
	await expect(dialog).toHaveCount(0);

	await buttons.last().click();
	await page.getByRole('dialog').getByRole('button', { name: 'Put it back' }).click();
	await expect(page.getByTestId('restore-job')).toContainText('Done', { timeout: 10_000 });
});

test('the caption says who chose the poster', async ({ page }) => {
	// Heat's poster is the owner's own file; Alien's the deploy policy wrote.
	await page.goto('/movies/1');
	await expect(page.getByTestId('poster-origin')).toHaveText('Your poster');
	await page.goto('/movies/2');
	await expect(page.getByTestId('poster-history').locator('li')).toHaveCount(1);
	await expect(page.getByTestId('poster-origin')).toHaveText('Marquee’s pick');

	// The same poster, written because the owner chose it, reads as theirs.
	await resetBackend({ deployedBy: 'owner' });
	await page.goto('/movies/2');
	await expect(page.getByTestId('poster-history').locator('li')).toHaveCount(1);
	await expect(page.getByTestId('poster-origin')).toHaveText('Your choice');
	await expect(page.locator('main')).not.toContainText('Marquee’s pick');
});

test('an unknown movie says so in words', async ({ page }) => {
	await page.goto('/movies/999');
	await expect(page.getByRole('alert')).toContainText('There is no such movie.');
	await expect(page.locator('body')).not.toContainText('{');
});
