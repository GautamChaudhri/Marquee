import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

test('television grid uses compact three-state deployment dots', async ({ page }) => {
	await page.goto('/television');

	await expect(page.getByRole('heading', { name: 'Television Library' })).toBeVisible();
	await expect(page.getByRole('button', { name: 'Grid view' })).toHaveAttribute(
		'aria-pressed',
		'true'
	);
	await expect(
		page.getByRole('button', {
			name: 'Open Complete Edition, 2018. Deployed: all 4 posters present'
		})
	).toBeVisible();
	await expect(
		page.getByRole('button', {
			name: 'Open Season Gap, 2020. Partially deployed: 4 of 9 posters present'
		})
	).toBeVisible();
	await expect(
		page.getByRole('button', {
			name: 'Open Series Gap, 2022. Partially deployed: 2 of 3 posters present'
		})
	).toBeVisible();
	await expect(
		page.getByRole('button', {
			name: 'Open The Long Combined Gap Case, 2024. Missing: none of 5 posters present'
		})
	).toBeVisible();
	await expect(page.getByText(/season posters? missing/i)).toHaveCount(0);

	const completeCard = page.locator('.cell').filter({ hasText: 'Complete Edition' });
	await expect(completeCard.getByText('2018', { exact: true })).toBeVisible();
	await expect(completeCard.getByText('Deployed', { exact: true })).toHaveCount(0);
});

test('movie and television view choices persist independently', async ({ page }) => {
	await page.goto('/television');
	await page.getByRole('button', { name: 'Table view' }).click();
	await expect(
		page.getByRole('table', { name: 'Television series and poster previews' })
	).toBeVisible();
	await page.reload();
	await expect(page.getByRole('button', { name: 'Table view' })).toHaveAttribute(
		'aria-pressed',
		'true'
	);

	await page.goto('/films');
	await page.getByRole('button', { name: 'Grid view' }).click();
	await expect(page.getByRole('button', { name: 'Grid view' })).toHaveAttribute(
		'aria-pressed',
		'true'
	);
	await page.reload();
	await expect(page.getByRole('button', { name: 'Grid view' })).toHaveAttribute(
		'aria-pressed',
		'true'
	);

	await page.goto('/television');
	await expect(page.getByRole('button', { name: 'Table view' })).toHaveAttribute(
		'aria-pressed',
		'true'
	);
	await expect
		.poll(() =>
			page.evaluate(() => ({
				movies: localStorage.getItem('marquee:filmMode'),
				television: localStorage.getItem('marquee:televisionMode')
			}))
		)
		.toEqual({ movies: '"grid"', television: '"list"' });
});

test('library names and counters live only in the global top bar', async ({ page }) => {
	await page.goto('/television');
	const televisionHeader = page.locator('header');
	await expect(televisionHeader.getByRole('heading', { name: 'Television Library' })).toBeVisible();
	await expect(televisionHeader.getByText('· 4 series', { exact: true })).toBeVisible();
	await expect(page.locator('.content').getByRole('heading', { level: 1 })).toHaveCount(0);

	await page.getByLabel('Search television titles').fill('Complete');
	await expect(page).toHaveURL(/q=Complete/);
	await expect(televisionHeader.getByText('· 4 series', { exact: true })).toBeVisible();

	await page.goto('/films');
	const filmHeader = page.locator('header');
	await expect(filmHeader.getByRole('heading', { name: 'Film Library' })).toBeVisible();
	await expect(filmHeader.getByText('· 4 movies', { exact: true })).toBeVisible();
	await expect(page.locator('.content').getByRole('heading', { level: 1 })).toHaveCount(0);

	await page.getByLabel('Filter films by artwork status').selectOption('approved');
	await expect(filmHeader.getByText('· 1 movie', { exact: true })).toBeVisible();
});

test('library table modes keep artwork status concise and poster-first', async ({ page }) => {
	await page.goto('/television');
	await page.getByRole('button', { name: 'Table view' }).click();

	const televisionTable = page.getByRole('table', {
		name: 'Television series and poster previews'
	});
	await expect(televisionTable).toBeVisible();
	await expect(televisionTable.getByRole('columnheader')).toHaveCount(1);
	await expect(
		televisionTable.getByRole('columnheader').filter({ hasText: 'Series and posters' })
	).toHaveCount(1);
	await expect(
		televisionTable.getByRole('columnheader').filter({ hasText: 'Artwork' })
	).toHaveCount(0);
	await expect(televisionTable.getByRole('columnheader').filter({ hasText: 'Genres' })).toHaveCount(
		0
	);
	await expect(televisionTable.getByText('Season art', { exact: true })).toHaveCount(0);
	await expect(televisionTable.getByText('In library', { exact: true })).toHaveCount(0);

	const completeRow = televisionTable.getByRole('row').filter({ hasText: 'Complete Edition' });
	await expect(
		completeRow.getByRole('img', { name: 'Deployed: all 4 posters present' })
	).toBeVisible();
	await expect(completeRow.getByText('Deployed', { exact: true })).toHaveCount(0);
	await expect(completeRow.getByText('Drama, Mystery', { exact: true })).toHaveCount(0);
	const completeStrip = completeRow.getByRole('region', {
		name: 'Complete Edition show and season posters'
	});
	await expect(completeStrip.getByText('Show', { exact: true })).toBeVisible();
	await expect(completeStrip.getByText('S00', { exact: true })).toBeVisible();
	await expect(completeStrip.getByText('S01', { exact: true })).toBeVisible();
	await expect(completeStrip.getByText('S02', { exact: true })).toBeVisible();
	await expect(completeStrip.locator('img')).toHaveCount(4);
	for (const image of await completeStrip.locator('img').all()) {
		await expect.poll(() => image.evaluate((element) => element.naturalWidth)).toBeGreaterThan(0);
	}

	const seasonGapRow = televisionTable.getByRole('row').filter({ hasText: 'Season Gap' });
	await expect(
		seasonGapRow.getByRole('img', { name: 'Partially deployed: 4 of 9 posters present' })
	).toBeVisible();
	await expect(seasonGapRow.getByText('Partially deployed', { exact: true })).toHaveCount(0);
	await expect(seasonGapRow.getByText('Science Fiction, Drama', { exact: true })).toHaveCount(0);
	const seasonGapStrip = seasonGapRow.getByRole('region', {
		name: 'Season Gap show and season posters'
	});
	await expect
		.poll(() => seasonGapStrip.evaluate((element) => element.scrollWidth > element.clientWidth))
		.toBe(true);
	await seasonGapStrip.focus();
	await expect(seasonGapStrip).toBeFocused();
	await page.keyboard.press('ArrowRight');
	await expect
		.poll(() => seasonGapStrip.evaluate((element) => element.scrollLeft))
		.toBeGreaterThan(0);

	const missingRow = televisionTable
		.getByRole('row')
		.filter({ hasText: 'The Long Combined Gap Case' });
	await expect(
		missingRow.getByRole('img', { name: 'Missing: none of 5 posters present' })
	).toBeVisible();
	await expect(missingRow.getByText('Missing', { exact: true })).toHaveCount(0);
	await expect(missingRow.getByText('S01', { exact: true })).toHaveCount(2);
	await expect(missingRow.locator('svg')).toHaveCount(0);
	await expect(missingRow.locator('.poster .dot')).toHaveCount(0);
	const missingShow = missingRow.locator('[data-poster-kind="show"] .poster');
	const missingSeason = missingRow.locator('[data-poster-kind="season"] .poster').first();
	await expect(missingShow).toHaveClass(/bottom-left-title/);
	await expect(missingSeason).toHaveClass(/centered-title/);
	await expect(missingShow).not.toHaveClass(/plain-fallback/);
	await expect(missingShow).toHaveCSS('background-image', /linear-gradient/);
	await expect(missingSeason).toHaveCSS('background-image', /linear-gradient/);

	const completeBox = await completeRow.boundingBox();
	const missingBox = await missingRow.boundingBox();
	expect(completeBox).not.toBeNull();
	expect(missingBox).not.toBeNull();
	expect(Math.abs(completeBox!.y - missingBox!.y)).toBeLessThan(2);
	expect(missingBox!.x).toBeGreaterThan(completeBox!.x);

	await page.setViewportSize({ width: 480, height: 800 });
	const narrowCompleteBox = await completeRow.boundingBox();
	const narrowMissingBox = await missingRow.boundingBox();
	expect(narrowCompleteBox).not.toBeNull();
	expect(narrowMissingBox).not.toBeNull();
	expect(narrowMissingBox!.y).toBeGreaterThan(narrowCompleteBox!.y);
	await page.setViewportSize({ width: 1280, height: 720 });

	await page.goto('/films');
	const filmsTable = page.getByRole('table', {
		name: 'Films and their artwork status and genres'
	});
	await expect(filmsTable).toBeVisible();
	await expect(filmsTable.getByRole('columnheader')).toHaveCount(3);
	await expect(filmsTable.getByText('Media', { exact: true })).toHaveCount(0);
	const midnightRow = filmsTable.getByRole('row').filter({ hasText: 'Midnight Archive' });
	await expect(midnightRow).toContainText('Deployed');
	await expect(midnightRow).not.toContainText('1080p');
	await expect(midnightRow).not.toContainText('MKV');
	await expect(midnightRow).toContainText('Thriller, Mystery');
	await expect(midnightRow.locator('svg')).toHaveCount(0);
	await expect(midnightRow.locator('.thumb img')).toHaveCount(1);
	await expect
		.poll(() => midnightRow.locator('.thumb img').evaluate((element) => element.naturalWidth))
		.toBeGreaterThan(0);
	const missingFilmThumb = filmsTable
		.getByRole('row')
		.filter({ hasText: 'Signal House' })
		.locator('.thumb .poster');
	await expect(missingFilmThumb).toHaveCSS('background-image', /linear-gradient/);
	await expect(missingFilmThumb.locator('.meta')).toHaveCount(0);
	await expect(filmsTable.getByText('Review', { exact: true })).toBeVisible();
	await expect(filmsTable.getByText('Approved', { exact: true })).toBeVisible();
	await expect(filmsTable.getByText('Missing', { exact: true })).toBeVisible();

	await page.getByLabel('Filter films by artwork status').selectOption('approved');
	await expect(page).toHaveURL(/poster_status=approved/);
	await expect(filmsTable.getByRole('row').filter({ hasText: 'Glass Harbor' })).toBeVisible();
	await expect(filmsTable.getByText('Midnight Archive', { exact: true })).toHaveCount(0);
	await page.getByLabel('Filter films by artwork status').selectOption('');
	await expect(page).not.toHaveURL(/poster_status=/);
	await page.getByLabel('Sort films').selectOption('year');
	await expect(page).toHaveURL(/sort=year/);
	await expect(filmsTable.locator('tbody tr').first()).toContainText('Signal House');
});

test('posters sidebar keeps Pipeline and exposes compact media workspace links', async ({
	page
}) => {
	await page.goto('/television');

	const pipeline = page.getByRole('link', { name: 'Pipeline', exact: true });
	const movies = page.getByRole('link', { name: 'Pipeline: Movies' });
	const television = page.getByRole('link', { name: 'Pipeline: TV' });
	await expect(pipeline).toHaveAttribute('href', '/pipeline');
	await expect(movies).toHaveAttribute('href', '/pipeline/movies');
	await expect(television).toHaveAttribute('href', '/pipeline/tv');

	await television.click();
	await expect(page).toHaveURL(/\/pipeline\/tv/);
	await expect(television).toHaveAttribute('aria-current', 'page');
	await expect(pipeline).not.toHaveAttribute('aria-current', 'page');

	await page.getByRole('button', { name: 'Toggle sidebar' }).click();
	await expect(page.locator('aside')).toHaveClass(/collapsed/);
	await expect
		.poll(() =>
			page
				.locator('.workspace-links')
				.evaluate((element) => getComputedStyle(element).flexDirection)
		)
		.toBe('column');
	const movieBox = await movies.boundingBox();
	const televisionBox = await television.boundingBox();
	expect(movieBox).not.toBeNull();
	expect(televisionBox).not.toBeNull();
	expect(televisionBox!.y).toBeGreaterThan(movieBox!.y);

	await page.goto('/pipeline/runs/detail00000000000000000000000001');
	await expect(pipeline).toHaveClass(/section-on/);
});

test('view controls support keyboard activation', async ({ page }) => {
	await page.goto('/television');

	const table = page.getByRole('button', { name: 'Table view' });
	await table.focus();
	await expect(table).toBeFocused();
	await page.keyboard.press('Enter');
	await expect(table).toHaveAttribute('aria-pressed', 'true');

	const grid = page.getByRole('button', { name: 'Grid view' });
	await grid.focus();
	await page.keyboard.press('Space');
	await expect(grid).toHaveAttribute('aria-pressed', 'true');
});

test('library grid and table views have no detectable accessibility violations', async ({
	page
}) => {
	await page.emulateMedia({ reducedMotion: 'reduce' });
	await page.goto('/television');
	await expect(page.getByRole('heading', { name: 'Television Library' })).toBeVisible();
	expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);

	await page.getByRole('button', { name: 'Table view' }).click();
	await expect(
		page.getByRole('table', { name: 'Television series and poster previews' })
	).toBeVisible();
	expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
	await page.getByRole('button', { name: 'Toggle theme' }).click();
	await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
	expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
	await page.getByRole('button', { name: 'Toggle theme' }).click();

	await page.goto('/films');
	await expect(
		page.getByRole('table', { name: 'Films and their artwork status and genres' })
	).toBeVisible();
	expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});
