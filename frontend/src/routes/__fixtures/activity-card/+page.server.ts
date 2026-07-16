import { env } from '$env/dynamic/private';
import { error } from '@sveltejs/kit';

export function load(): Record<string, never> {
	if (env.MARQUEE_E2E_FIXTURES !== '1') error(404, 'Not found');
	return {};
}
