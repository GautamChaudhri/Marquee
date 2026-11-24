import { redirect } from '@sveltejs/kit';
import { SESSION_COOKIE } from '$lib/server/session';
import type { RequestHandler } from './$types';

/** Forget the session cookie and go to sign-in. The same-origin check is in the hooks. */
export const POST: RequestHandler = ({ cookies, url }) => {
	cookies.delete(SESSION_COOKIE, {
		path: '/',
		httpOnly: true,
		sameSite: 'strict',
		secure: url.protocol === 'https:'
	});
	redirect(303, '/signin');
};
