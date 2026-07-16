/**
 * Svelte context boundary for the one browser-session `JobProgressStore` (A04).
 * A layout creates the store and provides it here; SSR renders each request with
 * its own store instance, so mutable state is never shared across requests.
 */
import { getContext, setContext } from 'svelte';
import { browserStoreDeps } from './browser-deps';
import { JobProgressStore, type JobProgressStoreDeps, type StoreCadence } from './store.svelte';

const KEY = Symbol('marquee:job-progress-store');

/** Create a store, defaulting to real browser dependencies. */
export function createJobProgressStore(
	deps?: JobProgressStoreDeps,
	cadence?: Partial<StoreCadence>
): JobProgressStore {
	return new JobProgressStore(deps ?? browserStoreDeps(), cadence);
}

/** Provide the store to descendants (call during a layout's component init). */
export function setJobProgressStore(store: JobProgressStore): JobProgressStore {
	return setContext(KEY, store);
}

/** Read the provided store; throws if no layout boundary provided one. */
export function getJobProgressStore(): JobProgressStore {
	const store = getContext<JobProgressStore | undefined>(KEY);
	if (!store) {
		throw new Error(
			'JobProgressStore is not available; call setJobProgressStore() in a layout boundary.'
		);
	}
	return store;
}
