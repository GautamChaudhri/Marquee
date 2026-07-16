import { svelteTesting } from '@testing-library/svelte/vite';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vitest/config';

/**
 * Two deterministic projects, one runner:
 *
 *   client — jsdom + Testing Library for Svelte components and browser-facing
 *            stores.  Files are named `*.svelte.test.ts`.
 *   server — node for pure logic (helpers, reducers, DI store state machines).
 *            Files are named `*.test.ts`.
 *
 * `vitest run` (the `test:unit` script) executes both once with no watch, so the
 * suite is reproducible in local and CI runs alike.  Tests use only synthetic
 * fixtures — never a real backend, media root, or operator database.
 */
export default defineConfig({
	plugins: [sveltekit()],
	test: {
		projects: [
			{
				extends: true,
				plugins: [svelteTesting()],
				test: {
					name: 'client',
					environment: 'jsdom',
					clearMocks: true,
					include: ['src/**/*.svelte.{test,spec}.{js,ts}'],
					exclude: ['src/lib/server/**'],
					setupFiles: ['./vitest-setup-client.ts']
				}
			},
			{
				extends: true,
				test: {
					name: 'server',
					environment: 'node',
					include: ['src/**/*.{test,spec}.{js,ts}'],
					exclude: ['src/**/*.svelte.{test,spec}.{js,ts}']
				}
			}
		]
	}
});
