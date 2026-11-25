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
