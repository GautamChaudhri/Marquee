import { fail, redirect } from '@sveltejs/kit';
import { apiKey } from '#lib/server/env.ts';
import { signInFailures } from '#lib/server/rate-limit.ts';
import {
	SESSION_COOKIE,
	SESSION_SECONDS,
	createSession,
	keyMatches,
	safeNext,
	verifySession
} from '#lib/server/session.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = ({ cookies, url }) => {
	const key = apiKey();
	if (key && verifySession(cookies.get(SESSION_COOKIE), key, Date.now())) {
		redirect(303, safeNext(url.searchParams.get('next')));
	}
	return { configured: key !== null };
};

export const actions: Actions = {
	default: async ({ request, cookies, url, getClientAddress }) => {
		const key = apiKey();
		if (!key) return fail(503, { message: 'No API key is configured on the web server.' });
		const client = getClientAddress();
		const waitMs = signInFailures.retryAfterMs(client, Date.now());
		if (waitMs > 0) {
			const seconds = Math.ceil(waitMs / 1000);
			return fail(429, { message: `Too many wrong keys. Try again in ${seconds} s.` });
		}
		const given = (await request.formData()).get('key');
		if (typeof given !== 'string' || !given || !keyMatches(given, key)) {
			signInFailures.fail(client, Date.now());
			return fail(401, { message: 'That is not the API key.' });
		}
		cookies.set(SESSION_COOKIE, createSession(key, Date.now()), {
			path: '/',
			httpOnly: true,
			sameSite: 'strict',
			secure: url.protocol === 'https:',
			maxAge: SESSION_SECONDS
		});
		redirect(303, safeNext(url.searchParams.get('next')));
	}
};
