import prettier from 'eslint-config-prettier';
import path from 'node:path';
import js from '@eslint/js';
import svelte from 'eslint-plugin-svelte';
import { defineConfig, includeIgnoreFile } from 'eslint/config';
import globals from 'globals';
import ts from 'typescript-eslint';

const gitignorePath = path.resolve(import.meta.dirname, '.gitignore');

export default defineConfig(
	includeIgnoreFile(gitignorePath),
	js.configs.recommended,
	ts.configs.recommended,
	svelte.configs.recommended,
	prettier,
	svelte.configs.prettier,
	{
		languageOptions: { globals: { ...globals.browser, ...globals.node } },
		rules: {
			// typescript-eslint strongly recommend that you do not use the no-undef lint rule on TypeScript projects.
			// see: https://typescript-eslint.io/troubleshooting/faqs/eslint/#i-get-errors-from-the-no-undef-rule-about-global-variables-not-being-defined-even-though-there-are-no-typescript-errors
			'no-undef': 'off'
		}
	},
	{
		files: ['**/*.svelte', '**/*.svelte.ts', '**/*.svelte.js'],
		languageOptions: {
			parserOptions: {
				projectService: true,
				extraFileExtensions: ['.svelte'],
				parser: ts.parser
			}
		}
	},
	{
		rules: {
			// We use plain string routes (`/films`, `/films/${id}`) intentionally;
			// the typed resolve() helper is overkill for this app.
			'svelte/no-navigation-without-resolve': 'off',
			// `crypto.randomUUID` is secure-context-only, so it is undefined when the UI
			// is served over plain HTTP at a LAN address. Callers build idempotency keys
			// with it, so the throw kills the request before it is sent.
			'no-restricted-syntax': [
				'error',
				{
					selector:
						"MemberExpression[object.name='crypto'][property.name='randomUUID'], MemberExpression[object.property.name='crypto'][property.name='randomUUID']",
					message: 'Use randomUuid() from $lib/uuid — crypto.randomUUID is secure-context-only.'
				}
			]
		}
	},
	{
		// The helper is the one place allowed to call it, behind a feature check.
		files: ['src/lib/uuid.ts'],
		rules: { 'no-restricted-syntax': 'off' }
	},
	{
		files: [
			'src/lib/components/subtitles/**/*.svelte',
			'src/routes/audio-subs/**/*.svelte',
			'src/routes/hdr/movies/+page.svelte'
		],
		rules: {
			// Existing subtitle/HDR screens still carry broad legacy patterns that predate
			// the current lint rules; keep the baseline green while touching unrelated areas.
			'@typescript-eslint/no-explicit-any': 'off',
			'@typescript-eslint/no-unused-vars': 'off',
			'svelte/require-each-key': 'off',
			'svelte/prefer-svelte-reactivity': 'off',
			'svelte/no-unused-svelte-ignore': 'off'
		}
	}
);
