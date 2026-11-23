import adapter from '@sveltejs/adapter-node';

/** @type {import('@sveltejs/kit').Config} */
const config = {
	kit: {
		adapter: adapter()
	},
	vitePlugin: {
		dynamicCompileOptions: ({ filename, compileOptions }) => {
			const isDependency = filename.split(/[/\\]/).includes('node_modules');
			if (!isDependency && !compileOptions.runes) {
				return { runes: true };
			}
		}
	}
};

export default config;
