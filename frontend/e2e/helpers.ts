import { expect, type Page } from '@playwright/test';
import { E2E_API_KEY, FAKE_URL } from '../playwright.config';

export async function resetBackend(): Promise<void> {
	const response = await fetch(`${FAKE_URL}/__fake/reset`, { method: 'POST' });
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
