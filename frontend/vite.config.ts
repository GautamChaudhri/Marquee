import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	server: {
		// Localhost only unless told otherwise (`npm run dev -- --host <address>`).
		port: 3166
	},
	plugins: [sveltekit()]
});
