import type { Page } from '@playwright/test';
import { expect, test } from './fixtures';
import { resetBackend, signIn } from './helpers';

test.beforeEach(async ({ page }) => {
	await resetBackend();
	await signIn(page);
});

/** Answer a run on the scratch-free fake: keep its pick without writing to the folder. */
async function keepPick(page: Page, runId: number) {
	await page.goto(`/review/${runId}`);
	await page.getByTestId('deploy-checkbox').uncheck();
	await page.getByRole('button', { name: 'Keep the pick' }).click();
	await expect(page.getByTestId('answer-result')).toBeVisible();
}

test('Taste shows the profile, agreement, the residual, and the exemplars', async ({ page }) => {
	await page.goto('/taste');
	const profile = page.getByTestId('profile');
	await expect(profile).toContainText('670b7c070000');
	await expect(profile).toContainText('Active: 59 liked, 0 disliked · Superseded: 1 liked');
	await expect(page.getByTestId('rebuild-due')).toHaveCount(0);
	const agreement = page.getByTestId('agreement');
	await expect(agreement).toContainText('No blind review yet.');
	await expect(agreement).toContainText('Not enough reviews yet for a percentage (0 of 10).');
	await expect(page.getByTestId('residual-state')).toHaveText(
		'Runs rank with the weighted scorer: it has not been trained yet; a rebuild trains it.'
	);
	await expect(page.getByTestId('residual')).toContainText(
		'It needs feedback on 25 more movies and 200 more pairs (0 of 25 movies, 0 of 200 pairs).'
	);
	await expect(page.getByText('No feedback yet.')).toBeVisible();

	const exemplars = page.getByTestId('exemplar');
	await expect(exemplars).toHaveCount(48);
	await expect(exemplars.first()).toContainText('Heat');
	await expect(exemplars.first()).toContainText('From your library');
	await page.getByRole('button', { name: 'Older' }).click();
	await expect(exemplars).toHaveCount(12);
	await page.getByLabel('State').selectOption('superseded');
	await expect(exemplars).toHaveCount(1);
	await expect(exemplars.first()).toContainText('Alien');
	await expect(exemplars.first()).toContainText('Replaced (before that was recorded)');
	await page.getByLabel('State').selectOption('');
	await page.getByLabel('Polarity').selectOption('negative');
	await expect(page.getByText('No exemplar matches.')).toBeVisible();
});

test('agreement counts blind reviews and keeps the others apart', async ({ page }) => {
	await keepPick(page, 12); // blind
	await keepPick(page, 13); // not blind
	await page.goto('/taste');
	const agreement = page.getByTestId('agreement');
	await expect(agreement).toContainText('You kept the pick in 1 of 1 blind review.');
	await expect(agreement).toContainText('Your choice was in the top 3 in 1 of 1.');
	await expect(agreement).toContainText(
		'1 review (pick kept in 1) of runs that were not blind, counted apart and never in the percentage.'
	);
	await expect(page.getByTestId('rebuild-due')).toBeVisible();
});

test('recent feedback lists answers with Undo and a link to the run', async ({ page }) => {
	await page.goto('/review/12');
	await page.getByTestId('deploy-checkbox').uncheck();
	await page
		.getByTestId('order-list')
		.locator('[data-key="s2"]')
		.getByRole('button', { name: /^Use / })
		.click();
	await expect(page.getByTestId('answer-result')).toBeVisible();

	await page.goto('/taste');
	const rows = page.getByTestId('recent-feedback').locator('li');
	await expect(rows).toHaveCount(1);
	await expect(rows.first()).toContainText('Film 05 (1955)');
	await expect(rows.first()).toContainText('Chose r12-1202.jpg');
	await expect(rows.first().getByRole('link', { name: 'Run 12' })).toHaveAttribute(
		'href',
		'/review/12'
	);
	await page.getByLabel('Polarity').selectOption('positive');
	await page.getByLabel('State').selectOption('superseded');
	// Alien's seed (replaced before that was recorded) and Film 05's, replaced by the answer.
	await expect(page.getByTestId('exemplar')).toHaveCount(2);
	await expect(page.getByTestId('exemplar').filter({ hasText: 'Film 05' })).toContainText(
		'Replaced by answer 1'
	);

	await rows.first().getByRole('button', { name: 'Undo' }).click();
	await expect(page.getByText('Answer 1 on Film 05 (1955) was undone.')).toBeVisible();
	await expect(rows).toHaveCount(2);
	await expect(rows.first()).toContainText('Undid answer 1');
	await expect(rows.nth(1)).toContainText('Undone by answer 2');
	await expect(rows.nth(1).getByRole('button', { name: 'Undo' })).toHaveCount(0);
});

test('Rebuild now follows its job to the end', async ({ page }) => {
	await page.goto('/taste');
	await page.getByRole('button', { name: 'Rebuild now' }).click();
	await expect(page.getByTestId('rebuild-job')).toContainText('Done', { timeout: 10_000 });
	// Asked again while one is queued: the API returns that job, and the page says so until
	// it ends.
	await page.getByRole('button', { name: 'Rebuild now' }).click();
	await expect(page.getByTestId('rebuild-job')).toContainText('Done', { timeout: 10_000 });
	await expect(page.getByText('A rebuild is already')).toHaveCount(0);
});
