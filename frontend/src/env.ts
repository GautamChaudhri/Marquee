/** The web server's environment variables, read through `$app/env/private` by
 *  `src/lib/server/env.ts` (https://svelte.dev/docs/kit/environment-variables).
 *
 *  Both are private (never sent to the browser) and not static (read when the server starts,
 *  never inlined into the build: the built app must hold no key). Both may be missing: the
 *  sign-in page says when no key is configured, and the API address has a default. */
import { defineEnvVars } from '@sveltejs/kit/env';

const optional = (value: string | undefined) => value;

export const variables = defineEnvVars({
	MARQUEE_API_URL: { public: false, static: false, schema: optional },
	MARQUEE_API_KEY: { public: false, static: false, schema: optional }
});
