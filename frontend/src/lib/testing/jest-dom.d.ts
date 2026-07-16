// Makes the `@testing-library/jest-dom` matchers (`toBeInTheDocument`,
// `toHaveTextContent`, ...) visible to `svelte-check`/`tsc` for every
// `*.svelte.test.ts` in the program. The matchers are registered at runtime by
// `vitest-setup-client.ts`; this file only surfaces their types. It lives under
// `src/` so it is part of the SvelteKit tsconfig program.
import '@testing-library/jest-dom/vitest';
