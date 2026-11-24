import { expect, test } from '@playwright/test';
import { APP_URL } from '../playwright.config';
import { lastBackendRequest, resetBackend, signIn } from './helpers';

test.beforeEach(async () => {
	await resetBackend();
});

test('a page without a session goes to sign-in, and comes back after it', async ({ page }) => {
	await page.goto('/movies/1');
	await expect(page).toHaveURL(/\/signin\?next=%2Fmovies%2F1$/);
	await expect(page.getByRole('heading', { name: 'Marquee' })).toBeVisible();

	await page.getByLabel('API key').fill('not-the-key');
	await page.getByRole('button', { name: 'Sign in' }).click();
	await expect(page.getByRole('alert')).toHaveText('That is not the API key.');
	await expect(page).toHaveURL(/\/signin/);

	await signIn(page, '/movies/1');
	await expect(page.getByRole('heading', { name: /Heat/ })).toBeVisible();
});

test('/api without a session is 401 and reaches nothing', async ({ request }) => {
	const response = await request.get('/api/movies');
	expect(response.status()).toBe(401);
	expect(await response.json()).toEqual({ detail: 'Sign in first.' });
	expect(await lastBackendRequest('/api/movies')).toEqual({});
});

test('the session cookie is HttpOnly, SameSite=Strict, and lasts 30 days', async ({ page }) => {
	await signIn(page);
	const cookies = await page.context().cookies();
	const session = cookies.find((cookie) => cookie.name === 'marquee_session');
	expect(session).toBeDefined();
	expect(session!.httpOnly).toBe(true);
	expect(session!.sameSite).toBe('Strict');
	expect(session!.path).toBe('/');
	expect(session!.secure).toBe(false); // served over plain HTTP here
	const days = (session!.expires * 1000 - Date.now()) / 86_400_000;
	expect(days).toBeGreaterThan(29.9);
	expect(days).toBeLessThan(30.1);
	// Page scripts cannot read it.
	expect(await page.evaluate(() => document.cookie)).not.toContain('marquee_session');
});

test('the proxy attaches the key; the browser never sends it', async ({ page }) => {
	await signIn(page);
	const answer = await page.evaluate(async () => {
		const response = await fetch('/api/worker');
		return { status: response.status, body: await response.json() };
	});
	expect(answer.status).toBe(200);
	expect(answer.body.running).toBe(true);
	expect(await lastBackendRequest('/api/worker')).toEqual({
		method: 'GET',
		path: '/api/worker',
		key_matched: true,
		cookie_forwarded: false,
		authorization_forwarded: false
	});
});

test('a cross-origin change is refused with 403, signed in or not', async ({ page, request }) => {
	const anonymous = await request.post('/api/sync', {
		headers: { origin: 'http://elsewhere.test' }
	});
	expect(anonymous.status()).toBe(403);

	await signIn(page);
	const signedIn = await page.request.post('/api/sync', {
		headers: { origin: 'http://elsewhere.test' }
	});
	expect(signedIn.status()).toBe(403);
	const noEvidence = await page.request.post('/api/sync');
	expect(noEvidence.status()).toBe(403);
	const ours = await page.request.post('/api/sync', { headers: { origin: APP_URL } });
	expect(ours.status()).toBe(202);
});

test('signing out ends the session', async ({ page }) => {
	await signIn(page);
	await page.getByRole('button', { name: 'Sign out' }).click();
	await expect(page).toHaveURL(/\/signin$/);
	const after = await page.request.get('/api/movies');
	expect(after.status()).toBe(401);
});
