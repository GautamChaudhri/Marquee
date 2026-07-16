/**
 * Real browser wiring for `JobProgressStore` dependencies. Kept separate from the
 * store so the state machine stays pure and injectable: tests never import this,
 * and this never has to be mocked.
 */
import { browser } from '$app/environment';
import type { EventSourceLike } from './client';
import type { JobProgressStoreDeps } from './store.svelte';

export function browserStoreDeps(): JobProgressStoreDeps {
	return {
		fetch: (input, init) => fetch(input, init),
		createEventSource: (url): EventSourceLike => {
			const source = new EventSource(url);
			return {
				addEventListener: (type, listener) =>
					source.addEventListener(type, listener as EventListener),
				close: () => source.close()
			};
		},
		now: () => Date.now(),
		schedule: (callback, delayMs) => {
			const id = setTimeout(callback, delayMs);
			return () => clearTimeout(id);
		},
		isHidden: () => browser && document.visibilityState === 'hidden',
		onVisibilityChange: (callback) => {
			if (!browser) return () => {};
			document.addEventListener('visibilitychange', callback);
			return () => document.removeEventListener('visibilitychange', callback);
		}
	};
}
