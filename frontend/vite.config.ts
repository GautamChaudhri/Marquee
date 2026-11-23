import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	build: {
		// Plotly is an intentional lazy-loaded vendor chunk; keep warnings for
		// unexpected application chunks while allowing its current minified size.
		chunkSizeWarningLimit: 5000
	},
	server: {
		// Bind all interfaces so the dev server works on any machine; override the
		// exposed address with `vite dev --host <ip>` when you need a specific one.
		host: true,
		port: 3166
	},
	plugins: [sveltekit()]
});
