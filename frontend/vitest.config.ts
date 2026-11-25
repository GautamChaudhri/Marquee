import { svelteTesting } from '@testing-library/svelte/vite';
import { defineConfig, mergeConfig } from 'vitest/config';
import viteConfig from './vite.config.ts';

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
 *
 * It extends `vite.config.ts`, so the tests compile components with the app's own
 * SvelteKit plugin and options.
 */
export default mergeConfig(
	viteConfig,
	defineConfig({
		test: {
			projects: [
				{
					extends: true,
					plugins: [svelteTesting()],
					test: {
						name: 'client',
						globals: true,
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
	})
);
