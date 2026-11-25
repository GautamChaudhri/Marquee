import { defineEnvVars } from '@sveltejs/kit/env';

// @migration-task Review dynamic environment variables. They accept missing values (`undefined`), add stricter validation where a value is required.
export const variables = defineEnvVars({
	MARQUEE_API_URL: { schema: (input) => input },
	MARQUEE_API_KEY: { schema: (input) => input }
});
