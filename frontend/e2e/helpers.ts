import { expect, type Page } from '@playwright/test';
import { E2E_API_KEY, FAKE_URL } from '../playwright.config';

/** Start the fake backend afresh. `review: 'none'` leaves no run waiting for a review;
 *  `problems` makes the system summary report a schema behind and a missing model;
 *  `evaluations` gives two finished blind evaluations, or those and one running, or those and
 *  a newer cancelled and a newer failed one (`stopped`); `residual:
 *  'active'` gives an active residual. */
export async function resetBackend(
	options: {
		review?: 'none';
		problems?: boolean;
		evaluations?: 'finished' | 'running' | 'stopped';
		residual?: 'active';
	} = {}
): Promise<void> {
	const params = new URLSearchParams();
	if (options.review) params.set('review', options.review);
	if (options.problems) params.set('problems', '1');
	if (options.evaluations) params.set('evaluations', options.evaluations);
	if (options.residual) params.set('residual', options.residual);
	const response = await fetch(`${FAKE_URL}/__fake/reset?${params}`, { method: 'POST' });
	expect(response.ok).toBe(true);
}

/** Every JSON body the fake backend received for `path`, oldest first. */
export async function receivedBodies(path: string): Promise<Record<string, unknown>[]> {
	return (await fetch(`${FAKE_URL}/__fake/received?path=${encodeURIComponent(path)}`)).json();
}

/** Make the fake answer the next `method` request for `path` with `status`, after
 *  receiving it. */
export async function failNext(path: string, status: number, method = 'POST'): Promise<void> {
	const params = new URLSearchParams({ path, status: String(status), method });
	const response = await fetch(`${FAKE_URL}/__fake/fail-next?${params}`, { method: 'POST' });
	expect(response.ok).toBe(true);
}

/** What the fake backend saw on the last request for `path`: whether the key matched and
 *  whether a cookie or an Authorization header came through. Never a value. Empty when the
 *  path was not requested since the last reset. */
export async function lastBackendRequest(path: string): Promise<{
	method?: string;
	path?: string;
	key_matched?: boolean;
	cookie_forwarded?: boolean;
	authorization_forwarded?: boolean;
}> {
	return (await fetch(`${FAKE_URL}/__fake/last?path=${encodeURIComponent(path)}`)).json();
}

export async function signIn(page: Page, next = '/'): Promise<void> {
	await page.goto(next === '/' ? '/signin' : `/signin?next=${encodeURIComponent(next)}`);
	await page.getByLabel('API key').fill(E2E_API_KEY);
	await page.getByRole('button', { name: 'Sign in' }).click();
	await page.waitForURL((url) => url.pathname === next.split('?')[0]);
}
