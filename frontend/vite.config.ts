import adapter from '@sveltejs/adapter-node';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	server: {
		// Localhost only unless told otherwise (`npm run dev -- --host <address>`).
		port: 3166
	},
	plugins: [
		sveltekit({
			adapter: adapter(),
			// The Content-Security-Policy of every page. SvelteKit adds a nonce for the inline
			// scripts it writes (and for app.html's, which carries %sveltekit.nonce%). Images and
			// every request come from this origin: the browser reaches the API through /api here.
			// Inline style attributes, and only those, are allowed: SvelteKit's own route
			// announcer (#svelte-announcer, in the root component it generates) carries one.
			csp: {
				mode: 'auto',
				directives: {
					'default-src': ['self'],
					'script-src': ['self'],
					'style-src': ['self'],
					'style-src-attr': ['unsafe-inline'],
					'img-src': ['self', 'data:', 'blob:'],
					'font-src': ['self'],
					'connect-src': ['self'],
					'object-src': ['none'],
					'base-uri': ['self'],
					'form-action': ['self'],
					'frame-ancestors': ['none']
				}
			},
			// This app's components are compiled in runes mode; dependencies keep their own.
			dynamicCompileOptions: ({ filename, compileOptions }) => {
				const isDependency = filename.split(/[/\\]/).includes('node_modules');
				if (!isDependency && !compileOptions.runes) {
					return { runes: true };
				}
			}
		})
	]
});
