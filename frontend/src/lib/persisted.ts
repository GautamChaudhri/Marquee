/** A writable store backed by localStorage, safe to import during SSR. */
import { browser } from '$app/environment';
import { writable, type Writable } from 'svelte/store';

export function persisted<T>(key: string, initial: T, apply?: (v: T) => void): Writable<T> {
	let start = initial;
	if (browser) {
		const raw = localStorage.getItem(key);
		if (raw !== null) {
			try {
				start = JSON.parse(raw) as T;
			} catch {
				start = raw as unknown as T;
			}
		}
	}
	const store = writable<T>(start);
	if (browser) {
		apply?.(start);
		store.subscribe((v) => {
			localStorage.setItem(key, JSON.stringify(v));
			apply?.(v);
		});
	}
	return store;
}
