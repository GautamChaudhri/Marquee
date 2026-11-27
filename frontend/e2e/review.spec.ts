import type { Locator, Page } from '@playwright/test';
import { expect, test } from './fixtures';
import { failNext, receivedBodies, resetBackend, signIn } from './helpers';

// Run 12 (Film 05, the owner's own poster) shows six designs, in rank order:
//   s1: 1201 (rank 1, the pick) and 1203 (1B) · s2: 1202 and 1205 · s3: 1204 and 1208
//   s4: 1206 (never downloaded) · s5: 1207 · c1209 (no stack, no score breakdown)
// It kept a large copy of 1201 and 1203 only.
const RUN12_SHOWN = [1201, 1202, 1204, 1206, 1207, 1209];
const FEEDBACK = '/api/runs/12/feedback';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

test.beforeEach(async ({ page }) => {
	await resetBackend();
	await signIn(page);
});

function card(page: Page, key: string): Locator {
	return page.getByTestId('order-list').locator(`[data-key="${key}"]`);
}

/** Choose a design the one way there is: open it from its card, then "Use this one". */
async function useDesign(page: Page, key: string) {
	await card(page, key).getByTestId('open-design').click();
	await page.getByTestId('design-dialog').getByTestId('use-this-one').click();
}

/** A popup rises into place as it opens; wait until it has settled before measuring it. */
async function settled(popup: Locator) {
	await popup.evaluate((el) => Promise.all(el.getAnimations().map((a) => a.finished)));
}

async function lastBody(path = FEEDBACK) {
	const bodies = await receivedBodies(path);
	return bodies[bodies.length - 1]!;
}

/** Drag a card by its handle onto a target, in small steps, as a person would. */
async function drag(page: Page, handle: Locator, target: Locator) {
	await handle.scrollIntoViewIfNeeded();
	const from = (await handle.boundingBox())!;
	const to = (await target.boundingBox())!;
	await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
	await page.mouse.down();
	await page.mouse.move(from.x + from.width / 2 + 5, from.y + from.height / 2 + 5, { steps: 5 });
	await page.mouse.move(to.x + to.width / 2, to.y + to.height / 3, { steps: 20 });
	await page.waitForTimeout(200);
	await page.mouse.up();
	await page.waitForTimeout(300);
}

test('the queue lists the runs waiting, with their pictures and marks', async ({ page }) => {
	await page.goto('/review');
	const rows = page.getByTestId('review-row');
	await expect(rows).toHaveCount(4);
	await expect(rows.first()).toContainText('Film 06');
	await expect(rows.first()).toContainText('The deploy policy already wrote the pick');
	const film05 = rows.filter({ hasText: 'Film 05' });
	await expect(film05).toContainText('Blind');
	await expect(film05).toContainText('9 ranked');
	await expect(film05.getByAltText('The poster in the folder now')).toHaveAttribute(
		'src',
		/\/api\/movies\/5\/poster\?width=160&v=/
	);
	await expect(film05.getByAltText('The poster in the folder now')).toHaveAttribute(
		'srcset',
		/\/api\/movies\/5\/poster\?width=160&v=\S+ 160w, \/api\/movies\/5\/poster\?width=320&v=\S+ 320w/
	);
	await expect(film05.getByAltText('The run’s pick')).toHaveAttribute(
		'src',
		'/api/runs/12/candidates/1201/image'
	);
	await expect(page.getByRole('navigation', { name: 'Main' })).toContainText('4');

	await film05.getByRole('link', { name: 'Review', exact: true }).click();
	await expect(page).toHaveURL(/\/review\/12$/);
	await expect(page.getByRole('heading', { name: /Film 05/ })).toBeVisible();
});

test('the queue’s cards stand in two columns, facts on top, and every part opens the run', async ({
	page
}) => {
	await page.goto('/review');
	const rows = page.getByTestId('review-row');
	const first = (await rows.nth(0).boundingBox())!;
	const second = (await rows.nth(1).boundingBox())!;
	expect(Math.abs(first.y - second.y)).toBeLessThan(1);
	expect(second.x).toBeGreaterThan(first.x + first.width - 1);

	// The Review button sits on the title's line, at the card's right.
	const film05 = rows.filter({ hasText: 'Film 05' });
	const heading = film05.getByRole('heading', { name: /Film 05/ });
	const button = film05.getByRole('link', { name: 'Review', exact: true });
	const title = (await heading.boundingBox())!;
	const go = (await button.boundingBox())!;
	expect(go.y).toBeLessThan(title.y + title.height);
	expect(go.x).toBeGreaterThan(title.x + title.width);

	// Below the facts, one row of small posters from the left: the current poster, then the
	// run's best designs in rank order (one poster a design, as the review page shows them).
	// Run 12 ranks six designs; the fourth was never downloaded. A card this narrow (1280 px
	// window) has six slots, so the sixth design waits for a wider card.
	const current = film05.getByTestId('queue-current');
	await expect(current.locator('figcaption')).toHaveText('Current');
	const designs = film05.getByTestId('queue-design');
	await expect(designs).toHaveCount(6);
	await expect(designs.locator('figcaption')).toHaveText(['Pick', '#2', '#3', '#4', '#5', '#6']);
	const images = ['1201', '1202', '1204', null, '1207', '1209'];
	for (const [i, id] of images.entries()) {
		if (id === null) await expect(designs.nth(i)).toContainText('No image');
		else
			await expect(designs.nth(i).locator('img')).toHaveAttribute(
				'src',
				`/api/runs/12/candidates/${id}/image`
			);
	}
	await expect(designs.filter({ visible: true })).toHaveCount(5);
	await expect(designs.nth(5)).toBeHidden();
	const card = (await film05.boundingBox())!;
	const now = (await current.boundingBox())!;
	expect(now.y).toBeGreaterThan(go.y + go.height);
	expect(now.x - card.x).toBeLessThan(20);
	expect(now.width).toBeLessThan(card.width / 5);
	for (let i = 0; i < 5; i++) {
		const box = (await designs.nth(i).boundingBox())!;
		expect(Math.abs(box.width - now.width)).toBeLessThan(1);
		expect(Math.abs(box.y - now.y)).toBeLessThan(1);
		expect(box.x).toBeGreaterThan(now.x + now.width);
	}
	const fifth = (await designs.nth(4).boundingBox())!;
	expect(fifth.x + fifth.width).toBeLessThanOrEqual(card.x + card.width);

	// A run with fewer designs shows what it has, the same size, from the same left edge; one
	// with none ranked shows that it has no pick to show.
	const film06 = rows.filter({ hasText: 'Film 06' });
	await expect(film06.getByTestId('queue-design')).toHaveCount(3);
	const card06 = (await film06.boundingBox())!;
	const now06 = (await film06.getByAltText('The poster in the folder now').boundingBox())!;
	const last06 = (await film06.getByTestId('queue-design').nth(2).boundingBox())!;
	expect(Math.abs(now06.x - card06.x - (now.x - card.x))).toBeLessThan(1);
	expect(Math.abs(last06.width - now.width)).toBeLessThan(1);
	// Two designs short of five: their two slots stay empty at the right.
	expect(card06.x + card06.width - (last06.x + last06.width)).toBeGreaterThan(2 * now.width);
	const film08 = rows.filter({ hasText: 'Film 08' });
	await expect(film08.getByTestId('queue-design')).toHaveCount(1);
	await expect(film08.getByTestId('queue-design')).toContainText('No image');

	// A wider card has room for seven slots: the sixth design shows, every poster the same
	// size, still in one row inside the card.
	await page.setViewportSize({ width: 1920, height: 900 });
	await expect(designs.filter({ visible: true })).toHaveCount(6);
	const wide = (await film05.boundingBox())!;
	const wideNow = (await current.boundingBox())!;
	const sixth = (await designs.nth(5).boundingBox())!;
	expect(Math.abs(sixth.width - wideNow.width)).toBeLessThan(1);
	expect(Math.abs(sixth.y - wideNow.y)).toBeLessThan(1);
	expect(sixth.x + sixth.width).toBeLessThanOrEqual(wide.x + wide.width);
	await page.setViewportSize({ width: 1280, height: 720 });

	for (const part of [
		film05.getByAltText('The poster in the folder now'),
		film05.getByAltText('The run’s pick'),
		film05.getByAltText('The design ranked 3'),
		heading.getByRole('link'),
		button
	]) {
		await page.goto('/review');
		await part.click();
		await expect(page).toHaveURL(/\/review\/12$/);
	}
});

test('the gallery shows each run’s pick alone, opens its review, and is remembered', async ({
	page
}) => {
	await page.goto('/review');
	const views = page.getByRole('group', { name: 'Show the queue as' });
	await expect(views.getByRole('button', { name: 'List' })).toHaveAttribute('aria-pressed', 'true');
	await views.getByRole('button', { name: 'Gallery' }).click();
	await expect(views.getByRole('button', { name: 'Gallery' })).toHaveAttribute(
		'aria-pressed',
		'true'
	);
	await expect(page.getByTestId('review-row')).toHaveCount(0);
	const tiles = page.getByTestId('review-tile');
	await expect(tiles).toHaveCount(4);
	await expect(tiles.first()).toContainText('Film 06');
	await expect(tiles.first()).toContainText('Written by policy');
	const film05 = tiles.filter({ hasText: 'Film 05' });
	await expect(film05).toContainText('Blind');
	await expect(film05.locator('img')).toHaveCount(1);
	await expect(film05.locator('img')).toHaveAttribute('src', '/api/runs/12/candidates/1201/image');
	await expect(tiles.filter({ hasText: 'Film 08' })).toContainText('No image');

	// How many posters each run ranked: the number alone in a round dot at the poster's top
	// right (screen readers hear "9 ranked").
	const count = film05.getByTestId('ranked-count');
	await expect(count).toHaveText('9 ranked');
	await expect(count).toHaveAttribute('title', '9 ranked');
	await expect(count.locator(':scope > span:not(.sr-only)')).toHaveText('9');
	await expect(tiles.filter({ hasText: 'Film 08' }).getByTestId('ranked-count')).toHaveText(
		'0 ranked'
	);
	const dot = (await count.boundingBox())!;
	const frame = (await film05.locator('img').boundingBox())!;
	expect(Math.abs(dot.width - dot.height)).toBeLessThan(1);
	expect(frame.x + frame.width - (dot.x + dot.width)).toBeLessThan(10);
	expect(dot.y - frame.y).toBeLessThan(10);
	expect(dot.y).toBeGreaterThanOrEqual(frame.y);

	await page.reload();
	await expect(page.getByTestId('review-tile')).toHaveCount(4);
	await film05.click();
	await expect(page).toHaveURL(/\/review\/12$/);

	await page.goto('/review');
	await page.getByRole('button', { name: 'List' }).click();
	await expect(page.getByTestId('review-row')).toHaveCount(4);
	await page.reload();
	await expect(page.getByTestId('review-row')).toHaveCount(4);
});

test('an empty queue says so', async ({ page }) => {
	await resetBackend({ review: 'none' });
	await page.goto('/review');
	await expect(page.getByText('Nothing is waiting for a review.')).toBeVisible();
});

test('the panel shows one card per design, the pick marked', async ({ page }) => {
	await page.goto('/review/12');
	const cards = page.getByTestId('order-list').getByTestId('review-card');
	await expect(cards).toHaveCount(6);
	await expect(cards.first()).toContainText('Pick');
	await expect(page.getByTestId('ranking-line')).toHaveText('Your order is the engine’s order.');
	// One rank on a card: the design's place in the order, counted from 1 with no gaps.
	await expect(page.getByTestId('order-list').getByTestId('design-rank')).toHaveText([
		'#1',
		'#2',
		'#3',
		'#4',
		'#5',
		'#6'
	]);
	// A yellow dot counts the variants, on the designs that have more than one.
	const dots = page.getByTestId('order-list').getByTestId('variant-count');
	await expect(dots).toHaveCount(3);
	await expect(cards.first().getByTestId('variant-count')).toHaveText('2 variants');
	await expect(cards.first().getByTestId('variant-count').locator('span').first()).toHaveText('2');
	await expect(card(page, 's5').getByTestId('variant-count')).toHaveCount(0);
	// A design never downloaded cannot be hated, and has nothing to look at closely.
	const never = card(page, 's4');
	await expect(never).toContainText('Never downloaded');
	await expect(never.getByRole('button', { name: /^Hate / })).toBeDisabled();
	await expect(never.getByTestId('zoom')).toBeDisabled();
	// No card chooses a poster by itself: that is done in the popup.
	await expect(page.getByTestId('order-list').getByRole('button', { name: /^Use / })).toHaveCount(
		0
	);
	// Images are the 500 px files, lazy, with their size set.
	const image = cards.first().locator('img');
	await expect(image).toHaveAttribute('src', '/api/runs/12/candidates/1201/image');
	await expect(image).toHaveAttribute('loading', 'lazy');
	await expect(image).toHaveAttribute('width', '500');
	await expect(image).toHaveAttribute('height', '750');
});

test('every card is the same height and carries its score as a bar', async ({ page }) => {
	await page.goto('/review/12');
	const cards = page.getByTestId('order-list').getByTestId('review-card');
	await expect(cards).toHaveCount(6);
	const boxes = await cards.evaluateAll((list) =>
		list.map((el) => {
			const box = el.getBoundingClientRect();
			return { top: Math.round(box.top), height: Math.round(box.height) };
		})
	);
	// With and without variants, with and without a picture: one height, one row.
	expect(new Set(boxes.map((box) => box.height)).size).toBe(1);
	expect(new Set(boxes.map((box) => box.top)).size).toBe(1);

	// The pick's bar: four families, together as wide as its score (0.93 of the track).
	const bar = cards.first().getByTestId('score-bar');
	await expect(bar).toHaveAttribute(
		'aria-label',
		'Score parts: Taste 0.37, Look 0.19, Content 0.23, Source 0.14'
	);
	const filled = await bar.evaluate((el) => {
		const track = el.getBoundingClientRect().width;
		const segments = [...el.children].map((child) => child.getBoundingClientRect().width);
		return { count: segments.length, share: segments.reduce((sum, w) => sum + w, 0) / track };
	});
	expect(filled.count).toBe(4);
	expect(filled.share).toBeGreaterThan(0.9);
	expect(filled.share).toBeLessThan(0.96);
	// A run that kept no breakdown for a poster shows an empty bar, at the same height.
	await expect(card(page, 'c1209').getByTestId('score-bar')).toHaveAttribute(
		'aria-label',
		'This run kept no score breakdown'
	);
	await expect(card(page, 'c1209').getByTestId('score-bar').locator('span')).toHaveCount(0);
});

test('Keep the pick asks before replacing the owner’s poster, then says what happened', async ({
	page
}) => {
	await page.goto('/review/12');
	await page.getByRole('button', { name: 'Keep the pick' }).click();
	const dialog = page.getByRole('dialog');
	await expect(dialog).toContainText('over your own poster');
	await dialog.getByRole('button', { name: 'Keep the pick' }).click();

	const result = page.getByTestId('answer-result');
	await expect(result).toContainText('Recorded as answer 1: Kept the pick');
	await expect(result.getByTestId('deploy-job')).toContainText('Done', { timeout: 10_000 });
	// The movie already has a liked poster, so keeping the pick changes no exemplar.
	await expect(result).toContainText('No rebuild was needed');
	await expect(result).toContainText('No exemplar changed.');
	const body = await lastBody();
	expect(body).toEqual({
		action: 'approval',
		shown: RUN12_SHOWN,
		deploy: true,
		idempotency_key: expect.stringMatching(UUID)
	});
	// The Review entry's number is read again after an answer.
	await expect(page.getByRole('navigation', { name: 'Main' })).toContainText('3');
});

test('Use this one sends that design; with the deploy off it does not ask', async ({ page }) => {
	await page.goto('/review/12');
	await page.getByTestId('deploy-checkbox').uncheck();
	await useDesign(page, 's2');
	// The popup closes with the choice, and with the deploy off nothing asks.
	await expect(page.getByRole('dialog')).toHaveCount(0);
	await expect(page.getByTestId('answer-result')).toContainText('Chose r12-1202.jpg');
	await expect(page.getByTestId('answer-folder')).toHaveText(
		'Nothing is written to the movie’s folder: you chose not to.'
	);
	const result = page.getByTestId('answer-result');
	await expect(result.getByTestId('deploy-job')).toHaveCount(0);
	await expect(result.getByTestId('rebuild-job')).toContainText('Done', { timeout: 10_000 });
	await expect(result.getByTestId('exemplar-changes')).toContainText('Made');
	await expect(result.getByTestId('exemplar-changes')).toContainText('Ended');
	expect(await lastBody()).toEqual({
		action: 'override',
		candidate_id: 1202,
		shown: RUN12_SHOWN,
		deploy: false,
		idempotency_key: expect.stringMatching(UUID)
	});
});

test('another variant of a stack can be the one its card shows and sends', async ({ page }) => {
	await page.goto('/review/12');
	const first = card(page, 's1');
	await page.getByTestId('deploy-checkbox').uncheck();
	await first.getByTestId('open-design').click();
	const dialog = page.getByTestId('design-dialog');
	await expect(dialog.getByRole('heading', { name: 'Design 1' })).toBeVisible();
	await expect(dialog).toContainText('2 variants, best first');
	// Inside the popup the variants are numbered within their design.
	const variants = dialog.getByTestId('variant');
	await expect(variants).toHaveCount(2);
	await expect(variants.nth(0)).toContainText('#1');
	await expect(variants.nth(1)).toContainText('#2');
	await expect(variants.nth(0)).toHaveAttribute('data-candidate', '1201');
	await expect(dialog.getByRole('button', { name: 'Select variant 1' })).toHaveAttribute(
		'aria-pressed',
		'true'
	);
	await expect(dialog.getByTestId('design-selection')).toHaveText('Variant 1 of 2 selected');
	await expect(dialog.getByTestId('design-detail')).toContainText('the run’s pick');

	await dialog.getByRole('button', { name: 'Select variant 2' }).click();
	await expect(dialog.getByRole('button', { name: 'Select variant 2' })).toHaveAttribute(
		'aria-pressed',
		'true'
	);
	await expect(dialog.getByTestId('design-selection')).toHaveText('Variant 2 of 2 selected');
	await expect(dialog.getByTestId('design-detail')).toContainText('0.890');
	// The selected variant is the one the card behind shows from now on.
	await expect(first).toHaveAttribute('data-candidate', '1203');
	// The proxy answers only after the backend has the body, so wait for that answer.
	const answered = page.waitForResponse(
		(response) =>
			response.request().method() === 'POST' && new URL(response.url()).pathname === FEEDBACK
	);
	await dialog.getByTestId('use-this-one').click();
	await answered;
	const body = await lastBody();
	expect(body.candidate_id).toBe(1203);
	expect(body.shown).toEqual([1203, 1202, 1204, 1206, 1207, 1209]);
});

test('choosing the pick with Use this one is recorded, and shown, as keeping it', async ({
	page
}) => {
	await page.goto('/review/12');
	await page.getByTestId('deploy-checkbox').uncheck();
	await useDesign(page, 's1');
	const result = page.getByTestId('answer-result');
	await expect(result).toContainText('Kept the pick');
	await expect(result).toContainText('recorded as keeping the pick');
	expect((await lastBody()).action).toBe('override');
});

test('a lone poster is chosen from the same popup, with the button in the same place', async ({
	page
}) => {
	await page.goto('/review/12');
	await page.getByTestId('deploy-checkbox').uncheck();
	const dialog = page.getByTestId('design-dialog');
	/** Where "Use this one" sits, from the popup's bottom right corner. */
	const corner = async () => {
		const panel = (await dialog.boundingBox())!;
		const button = (await dialog.getByTestId('use-this-one').boundingBox())!;
		return {
			right: Math.round(panel.x + panel.width - (button.x + button.width)),
			bottom: Math.round(panel.y + panel.height - (button.y + button.height))
		};
	};

	await card(page, 's1').getByTestId('open-design').click();
	const withVariants = await corner();
	await dialog.getByRole('button', { name: 'Close', exact: true }).click();
	await expect(dialog).toHaveCount(0);

	await card(page, 's5').getByTestId('open-design').click();
	await expect(dialog.getByRole('heading', { name: 'Design 5' })).toBeVisible();
	await expect(dialog).toContainText('1 poster');
	await expect(dialog.getByTestId('variant')).toHaveCount(1);
	await expect(dialog.getByTestId('design-selection')).toHaveText('The only poster of this design');
	expect(await corner()).toEqual(withVariants);
	// The spare room names every part of the score, the largest first.
	const rows = dialog.getByTestId('score-breakdown').locator('tbody tr');
	await expect(rows).toHaveCount(4);
	await expect(rows.first()).toContainText('Taste match');
	await expect(rows.first()).toContainText('+0.32');
	await expect(rows.nth(1)).toContainText('Faces');

	await dialog.getByTestId('use-this-one').click();
	await expect(dialog).toHaveCount(0);
	await expect(page.getByTestId('answer-result')).toContainText('Chose r12-1207.jpg');
	expect((await lastBody()).candidate_id).toBe(1207);
});

test('the popup says when a design cannot be chosen, and can hate or bring one back', async ({
	page
}) => {
	await page.goto('/review/12');
	const dialog = page.getByTestId('design-dialog');
	// Never downloaded: it opens, but nothing in it can choose or hate it.
	await card(page, 's4').getByTestId('open-design').click();
	await expect(dialog.getByTestId('design-selection')).toHaveText(
		'Never downloaded: it cannot be chosen or hated.'
	);
	await expect(dialog.getByTestId('use-this-one')).toBeDisabled();
	await expect(dialog.getByRole('button', { name: 'Hate this design' })).toBeDisabled();
	// A poster with no breakdown says so.
	await page.keyboard.press('Escape');
	await expect(dialog).toHaveCount(0);
	await card(page, 'c1209').getByTestId('open-design').click();
	await expect(dialog.getByTestId('score-breakdown-none')).toBeVisible();
	await dialog.getByRole('button', { name: 'Close', exact: true }).click();

	// Hating from the popup closes it and moves the design to the hate pile.
	await card(page, 's3').getByTestId('open-design').click();
	await dialog.getByRole('button', { name: 'Hate this design' }).click();
	await expect(dialog).toHaveCount(0);
	const pile = page.getByTestId('hate-pile').getByTestId('review-card');
	await expect(pile).toHaveCount(1);
	await expect(pile.first()).toContainText('Hated');
	// The order closes up: no gap is left where it stood.
	await expect(page.getByTestId('order-list').getByTestId('design-rank')).toHaveText([
		'#1',
		'#2',
		'#3',
		'#4',
		'#5'
	]);
	await pile.first().getByTestId('open-design').click();
	await expect(dialog.getByRole('heading', { name: 'Hated design 1' })).toBeVisible();
	await dialog.getByRole('button', { name: 'Bring back' }).click();
	await expect(dialog).toHaveCount(0);
	await expect(pile).toHaveCount(0);
});

test('the popup is a gallery: many variants fit, and a click on the backdrop closes it', async ({
	page
}) => {
	await page.goto('/review/12');
	await card(page, 's2').getByTestId('open-design').click();
	const dialog = page.getByTestId('design-dialog');
	const tiles = dialog.getByTestId('variant');
	await expect(tiles).toHaveCount(2);
	// Small posters, so a design with many variants shows a whole row of them at once.
	const gallery = (await dialog.getByTestId('variants').boundingBox())!;
	const tile = (await tiles.first().boundingBox())!;
	expect(tile.width).toBeLessThan(200);
	expect(Math.floor(gallery.width / tile.width)).toBeGreaterThanOrEqual(4);
	// Every variant keeps its magnifier and its own score bar.
	await expect(dialog.getByRole('button', { name: /^Look closer at variant/ })).toHaveCount(2);
	await expect(tiles.getByTestId('score-bar')).toHaveCount(2);

	await page.mouse.click(8, 8);
	await expect(dialog).toHaveCount(0);
});

test('a popup holds the page still, and a long gallery scrolls inside it', async ({ page }) => {
	await resetBackend({ review: 'many' });
	await page.goto('/review/12');
	const scrollY = () => page.evaluate(() => window.scrollY);
	const viewport = page.viewportSize()!;
	// The page itself is longer than the window and scrolls.
	await page.mouse.move(viewport.width / 2, viewport.height / 2);
	await page.mouse.wheel(0, 200);
	await expect.poll(scrollY).toBeGreaterThan(0);
	const before = await scrollY();

	await card(page, 's6').getByTestId('open-design').click();
	const dialog = page.getByTestId('design-dialog');
	await expect(dialog.getByTestId('variant')).toHaveCount(24);
	await expect(page.locator('html')).toHaveClass(/mq-modal-open/);
	// The whole popup is on screen, with the button that chooses a poster.
	await settled(dialog);
	const panel = (await dialog.boundingBox())!;
	expect(panel.y).toBeGreaterThanOrEqual(0);
	expect(panel.y + panel.height).toBeLessThanOrEqual(viewport.height);
	await expect(dialog.getByTestId('use-this-one')).toBeInViewport();
	// The score details stand on the left, the gallery on the right.
	const gallery = dialog.getByTestId('variants');
	const detail = (await dialog.getByTestId('design-detail').boundingBox())!;
	const galleryBox = (await gallery.boundingBox())!;
	expect(detail.x + detail.width).toBeLessThanOrEqual(galleryBox.x);
	// 24 variants do not fit: the gallery is what scrolls, and nothing else moves.
	const scroller = () =>
		gallery.evaluate((el) => ({ top: el.scrollTop, room: el.scrollHeight - el.clientHeight }));
	expect((await scroller()).room).toBeGreaterThan(100);
	await page.mouse.move(galleryBox.x + galleryBox.width / 2, galleryBox.y + galleryBox.height / 2);
	await page.mouse.wheel(0, 400);
	await expect.poll(async () => (await scroller()).top).toBeGreaterThan(0);
	expect(await scrollY()).toBe(before);
	expect((await dialog.boundingBox())!.y).toBe(panel.y);
	await expect(dialog.getByRole('heading', { name: 'Design 6' })).toBeInViewport();
	await expect(dialog.getByTestId('use-this-one')).toBeInViewport();
	// Scrolled to its end, the gallery does not hand the wheel on to the page.
	await page.mouse.wheel(0, 20_000);
	await expect.poll(async () => (await scroller()).room - (await scroller()).top).toBeLessThan(2);
	await page.mouse.wheel(0, 400);
	// Nor does the wheel over the score details, or over the dimmed page.
	await page.mouse.move(detail.x + detail.width / 2, detail.y + detail.height / 2);
	await page.mouse.wheel(0, 400);
	await page.mouse.move(6, viewport.height / 2);
	await page.mouse.wheel(0, 400);
	await page.waitForTimeout(150);
	expect(await scrollY()).toBe(before);
	// The last variant can be reached and chosen from.
	await dialog.getByRole('button', { name: 'Select variant 24' }).click();
	await expect(dialog.getByTestId('design-selection')).toHaveText('Variant 24 of 24 selected');

	// Closed, the page scrolls again.
	await page.keyboard.press('Escape');
	await expect(dialog).toHaveCount(0);
	await expect(page.locator('html')).not.toHaveClass(/mq-modal-open/);
	await page.mouse.move(viewport.width / 2, viewport.height / 2);
	await page.mouse.wheel(0, 200);
	await expect.poll(scrollY).toBeGreaterThan(before);
});

test('the magnifier shows one poster large and steps through its design', async ({ page }) => {
	await page.goto('/review/12');
	const lightbox = page.getByTestId('lightbox');
	const image = lightbox.getByTestId('lightbox-image');
	const caption = lightbox.getByTestId('lightbox-caption');

	// From a card: the run's large copy when it kept one.
	await card(page, 's1').getByTestId('zoom').click();
	await expect(image).toHaveAttribute('src', '/api/runs/12/candidates/1201/image/large');
	await expect(caption).toHaveText('Design 1 · variant 1 of 2 · score 0.930 · 1000 × 1500 px');
	// The copy is 1000 by 1500 px, far taller than the window: it is scaled down to fit, whole
	// and in its own shape, and is still much larger than on its card.
	await expect(image).toHaveJSProperty('naturalHeight', 1500);
	await settled(lightbox);
	const shown = (await image.boundingBox())!;
	const viewport = page.viewportSize()!;
	expect(shown.height).toBeGreaterThan(viewport.height * 0.7);
	expect(shown.y).toBeGreaterThanOrEqual(0);
	expect(shown.y + shown.height).toBeLessThanOrEqual(viewport.height);
	expect(shown.x).toBeGreaterThanOrEqual(0);
	expect(shown.x + shown.width).toBeLessThanOrEqual(viewport.width);
	expect(shown.width / shown.height).toBeCloseTo(2 / 3, 1);
	// The caption under it is on screen too.
	const captionBox = (await caption.boundingBox())!;
	expect(captionBox.y + captionBox.height).toBeLessThanOrEqual(viewport.height);
	// The arrows, on the keyboard and on screen, go round the design's variants.
	await page.keyboard.press('ArrowRight');
	await expect(image).toHaveAttribute('data-candidate', '1203');
	await expect(caption).toHaveText('Design 1 · variant 2 of 2 · score 0.890 · 1000 × 1500 px');
	await lightbox.getByRole('button', { name: 'Next variant' }).click();
	await expect(image).toHaveAttribute('data-candidate', '1201');
	await page.keyboard.press('ArrowLeft');
	await expect(image).toHaveAttribute('data-candidate', '1203');
	await page.keyboard.press('Escape');
	await expect(lightbox).toHaveCount(0);

	// A run that kept no large copy shows its 500 px file, at the same size on screen, and
	// the caption gives that file's own resolution.
	await card(page, 's5').getByTestId('zoom').click();
	await expect(image).toHaveAttribute('src', '/api/runs/12/candidates/1207/image');
	await settled(lightbox);
	const small = (await image.boundingBox())!;
	expect(Math.abs(small.height - shown.height)).toBeLessThanOrEqual(1);
	// The fake backend's small file is 2 by 3 px.
	await expect(caption).toHaveText('Design 5 · score 0.810 · 2 × 3 px');
	await expect(lightbox.getByRole('button', { name: 'Next variant' })).toHaveCount(0);
	await lightbox.getByRole('button', { name: 'Close', exact: true }).click();
	await expect(lightbox).toHaveCount(0);

	// From the popup: it opens over the popup, and Escape closes one layer at a time.
	await card(page, 's2').getByTestId('open-design').click();
	const dialog = page.getByTestId('design-dialog');
	await dialog.getByRole('button', { name: 'Look closer at variant 2' }).click();
	await expect(image).toHaveAttribute('data-candidate', '1205');
	await page.keyboard.press('Escape');
	await expect(lightbox).toHaveCount(0);
	await expect(dialog).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(dialog).toHaveCount(0);
});

test('Save this ranking: reorder and hate with buttons, confirm, send the order', async ({
	page
}) => {
	await page.goto('/review/12');
	await card(page, 's3')
		.getByRole('button', { name: /^Move .* up$/ })
		.click();
	await card(page, 's3')
		.getByRole('button', { name: /^Move .* up$/ })
		.click();
	await card(page, 's2')
		.getByRole('button', { name: /^Hate / })
		.click();
	await card(page, 's5')
		.getByRole('button', { name: /^Hate / })
		.click();
	await expect(page.getByTestId('hate-pile').getByTestId('review-card')).toHaveCount(2);
	await expect(page.getByTestId('ranking-line')).toHaveText(
		'Your order differs from the engine’s. 2 designs are in the hate pile.'
	);
	// Bring one back and hate it again: it goes to the end of the order, then back.
	await page
		.getByTestId('hate-pile')
		.getByRole('button', { name: 'Bring back hated design 2' })
		.click();
	await card(page, 's5')
		.getByRole('button', { name: /^Hate / })
		.click();

	await page.getByRole('button', { name: 'Save this ranking' }).click();
	const dialog = page.getByRole('dialog');
	await expect(dialog).toContainText(
		'The design at the top of your order is written to the movie’s folder'
	);
	await expect(dialog).toContainText('The 2 hated designs become examples of what you do not want');
	await dialog.getByRole('button', { name: 'Save this ranking' }).click();
	await expect(page.getByTestId('answer-result')).toContainText('Ranked 4 designs');
	expect(await lastBody()).toEqual({
		action: 'rank',
		order: [1204, 1201, 1206, 1209],
		hated: [1202, 1207],
		shown: RUN12_SHOWN,
		deploy: true,
		idempotency_key: expect.stringMatching(UUID)
	});
});

test('a design can be dragged to a new place and into the hate pile', async ({ page }) => {
	await page.goto('/review/12');
	await drag(page, card(page, 'c1209').getByRole('button', { name: /^Drag / }), card(page, 's1'));
	await expect(page.getByTestId('order-list').getByTestId('review-card').first()).toHaveAttribute(
		'data-key',
		'c1209'
	);
	await drag(
		page,
		card(page, 's2').getByRole('button', { name: /^Drag / }),
		page.getByTestId('hate-pile')
	);
	await expect(page.getByTestId('hate-pile').getByTestId('review-card')).toHaveCount(1);
	await page.getByRole('button', { name: 'Save this ranking' }).click();
	await page.getByRole('dialog').getByRole('button', { name: 'Save this ranking' }).click();
	await expect(page.getByTestId('answer-result')).toBeVisible();
	const body = await lastBody();
	expect(body.order).toEqual([1209, 1201, 1204, 1206, 1207]);
	expect(body.hated).toEqual([1202]);
});

test('the panel works from the keyboard alone', async ({ page }) => {
	await page.goto('/review/12');
	// A design opens and closes from the keyboard, and focus comes back to its card.
	await card(page, 's5').getByTestId('open-design').focus();
	await page.keyboard.press('Enter');
	await expect(page.getByTestId('design-dialog')).toBeFocused();
	await page.keyboard.press('Escape');
	await expect(page.getByTestId('design-dialog')).toHaveCount(0);
	await expect(card(page, 's5').getByTestId('open-design')).toBeFocused();
	await card(page, 's5')
		.getByRole('button', { name: /^Move .* up$/ })
		.focus();
	await page.keyboard.press('Enter');
	await card(page, 's2')
		.getByRole('button', { name: /^Hate / })
		.focus();
	await page.keyboard.press('Space');
	await page.getByTestId('deploy-checkbox').focus();
	await page.keyboard.press('Space');
	await expect(page.getByTestId('deploy-checkbox')).not.toBeChecked();
	await page.getByRole('button', { name: 'Save this ranking' }).focus();
	await page.keyboard.press('Enter');
	await expect(page.getByRole('dialog')).toContainText('nothing is written to the folder');
	await page.getByRole('dialog').getByRole('button', { name: 'Save this ranking' }).focus();
	await page.keyboard.press('Enter');
	await expect(page.getByTestId('answer-result')).toContainText('Ranked 5 designs');
	const body = await lastBody();
	expect(body.order).toEqual([1201, 1204, 1207, 1206, 1209]);
	expect(body.hated).toEqual([1202]);
	expect(body.deploy).toBe(false);
});

test('None of these asks, then sends a reject with no deploy', async ({ page }) => {
	await page.goto('/review/13');
	await page.getByRole('button', { name: 'None of these' }).click();
	const dialog = page.getByRole('dialog');
	await expect(dialog).toContainText('Nothing is written to the movie’s folder');
	await expect(dialog).toContainText('The 3 designs shown become examples');
	await dialog.getByRole('button', { name: 'None of these' }).click();
	await expect(page.getByTestId('answer-folder')).toHaveText(
		'Nothing is written to the movie’s folder.'
	);
	expect(await lastBody('/api/runs/13/feedback')).toEqual({
		action: 'reject',
		shown: [1301, 1302, 1303],
		idempotency_key: expect.stringMatching(UUID)
	});
});

test('Undo takes the answer back and says the poster was not put back; Next moves on', async ({
	page
}) => {
	await page.goto('/review/12');
	await page.getByTestId('deploy-checkbox').uncheck();
	await page.getByRole('button', { name: 'Keep the pick' }).click();
	const result = page.getByTestId('answer-result');
	await expect(result).toContainText('Recorded as answer 1');
	await result.getByRole('button', { name: 'Undo' }).click();
	const undone = page.getByTestId('undo-result');
	await expect(undone).toContainText('The poster in the folder was not put back.');
	await expect(undone.getByRole('link')).toHaveAttribute('href', '/movies/5');
	await expect(result.getByRole('button', { name: 'Undo' })).toHaveCount(0);
	expect(await receivedBodies('/api/feedback/1/revoke')).toEqual([]);

	await result.getByRole('button', { name: 'Next' }).click();
	await expect(page).toHaveURL(/\/review\/13$/);
	await expect(page.getByRole('heading', { name: /Film 06/ })).toBeVisible();
	await expect(page.getByTestId('answer-result')).toHaveCount(0);
});

test('a run already answered lists its answers, newest first, and can be answered again', async ({
	page
}) => {
	await page.goto('/review/12');
	await page.getByTestId('deploy-checkbox').uncheck();
	await page.getByRole('button', { name: 'Keep the pick' }).click();
	await expect(page.getByTestId('answer-result')).toBeVisible();
	await useDesign(page, 's2');
	await expect(page.getByTestId('answer-result')).toContainText('Recorded as answer 2');

	await page.reload();
	const events = page.getByTestId('run-events').locator('li');
	await expect(events).toHaveCount(2);
	await expect(events.first()).toContainText('Chose r12-1202.jpg');
	await expect(events.nth(1)).toContainText('Kept the pick');
	await expect(page.getByRole('heading', { name: 'Answer again' })).toBeVisible();

	await events.first().getByRole('button', { name: 'Undo' }).click();
	await expect(page.getByText('Answer 2 was undone.')).toBeVisible();
	await expect(events).toHaveCount(3);
	await expect(events.first()).toContainText('Undid answer 2');
	await expect(events.nth(1)).toContainText('Undone by answer 3');
	await expect(events.nth(1).getByRole('button', { name: 'Undo' })).toHaveCount(0);
	await expect(events.first().getByRole('button', { name: 'Undo' })).toHaveCount(0);
});

test('a run that cannot be answered is read-only and says why', async ({ page }) => {
	await page.goto('/review/10');
	await expect(page.getByText('This is not the movie’s latest completed run.')).toBeVisible();
	await expect(page.getByRole('link', { name: 'Review the latest run (12)' })).toHaveAttribute(
		'href',
		'/review/12'
	);
	await expect(page.getByRole('button', { name: 'Keep the pick' })).toHaveCount(0);

	await page.goto('/review/16');
	await expect(
		page.getByText('This run is running. Only a completed run can be answered.')
	).toBeVisible();
	await page.goto('/review/17');
	await expect(page.getByText('No candidate was ranked in this run')).toBeVisible();
	await expect(page.getByRole('button', { name: 'None of these' })).toHaveCount(0);
	await page.goto('/review/999');
	await expect(page.getByRole('alert')).toContainText('There is no such run.');
});

test('a request retried after a failure keeps its key; the next answer gets a new one', async ({
	page
}) => {
	await failNext(FEEDBACK, 502);
	await page.goto('/review/12');
	await page.getByTestId('deploy-checkbox').uncheck();
	await page.getByRole('button', { name: 'Keep the pick' }).click();
	const failed = page.getByRole('alert').filter({ hasText: 'Your answer was not recorded' });
	await expect(failed).toBeVisible();
	await failed.getByRole('button', { name: 'Try again' }).click();
	await expect(page.getByTestId('answer-result')).toContainText('Recorded as answer 1');

	await useDesign(page, 's2');
	await expect(page.getByTestId('answer-result')).toContainText('Recorded as answer 2');
	const bodies = await receivedBodies(FEEDBACK);
	expect(bodies).toHaveLength(3);
	expect(bodies[1]!.idempotency_key).toBe(bodies[0]!.idempotency_key);
	expect(bodies[2]!.idempotency_key).not.toBe(bodies[0]!.idempotency_key);
});

test('the movie page links its completed run to its review', async ({ page }) => {
	await page.goto('/movies/1');
	await expect(page.getByTestId('review-this-run')).toHaveAttribute('href', '/review/11');
	await expect(page.getByRole('button', { name: /keep|use this|save this|none of/i })).toHaveCount(
		0
	);
});
