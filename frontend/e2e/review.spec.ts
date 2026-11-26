import { expect, test, type Locator, type Page } from '@playwright/test';
import { failNext, receivedBodies, resetBackend, signIn } from './helpers';

// Run 12 (Film 05, the owner's own poster) shows six designs, in rank order:
//   s1: 1201 (rank 1, the pick) and 1203 (1B) · s2: 1202 and 1205 · s3: 1204 and 1208
//   s4: 1206 (never downloaded) · s5: 1207 · c1209 (no stack)
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
	await expect(film05.getByAltText('The run’s pick')).toHaveAttribute(
		'src',
		'/api/runs/12/candidates/1201/image'
	);
	await expect(page.getByRole('navigation', { name: 'Main' })).toContainText('4');

	await film05.getByRole('link', { name: 'Review', exact: true }).click();
	await expect(page).toHaveURL(/\/review\/12$/);
	await expect(page.getByRole('heading', { name: /Film 05/ })).toBeVisible();
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
	await expect(cards.first()).toContainText('2 variants');
	await expect(page.getByTestId('ranking-line')).toHaveText('Your order is the engine’s order.');
	// A design never downloaded cannot be chosen or hated.
	const never = card(page, 's4');
	await expect(never).toContainText('Never downloaded');
	await expect(never.getByRole('button', { name: /^Use / })).toBeDisabled();
	await expect(never.getByRole('button', { name: /^Hate / })).toBeDisabled();
	// Images are the 500 px files, lazy, with their size set.
	const image = cards.first().getByAltText('Candidate ranked 1');
	await expect(image).toHaveAttribute('loading', 'lazy');
	await expect(image).toHaveAttribute('width', '500');
	await expect(image).toHaveAttribute('height', '750');
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
	await card(page, 's2').getByRole('button', { name: /^Use / }).click();
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
	await first.getByTestId('variants-toggle').click();
	await first.getByRole('button', { name: /Show variant 1B/ }).click();
	await expect(first).toHaveAttribute('data-candidate', '1203');
	await page.getByTestId('deploy-checkbox').uncheck();
	// The proxy answers only after the backend has the body, so wait for that answer.
	const answered = page.waitForResponse(
		(response) =>
			response.request().method() === 'POST' && new URL(response.url()).pathname === FEEDBACK
	);
	await first.getByRole('button', { name: /^Use / }).click();
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
	await card(page, 's1').getByRole('button', { name: /^Use / }).click();
	const result = page.getByTestId('answer-result');
	await expect(result).toContainText('Kept the pick');
	await expect(result).toContainText('recorded as keeping the pick');
	expect((await lastBody()).action).toBe('override');
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
		.getByRole('button', { name: /Bring back design ranked 7/ })
		.click();
	await card(page, 's5')
		.getByRole('button', { name: /^Hate / })
		.click();

	await page.getByRole('button', { name: 'Save this ranking' }).click();
	const dialog = page.getByRole('dialog');
	await expect(dialog).toContainText('The design ranked 4 is written to the movie’s folder');
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
	await card(page, 's2').getByRole('button', { name: /^Use / }).click();
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

	await card(page, 's2').getByRole('button', { name: /^Use / }).click();
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
