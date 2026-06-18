/** Theme + persisted-UI stores. The `[data-theme]` attribute lives on <html>;
 *  an inline script in app.html sets it pre-render to avoid a flash. */
import { browser } from '$app/environment';
import { writable, type Writable } from 'svelte/store';

export type Theme = 'dark' | 'light';

function persisted<T>(key: string, initial: T, apply?: (v: T) => void): Writable<T> {
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

export const theme = persisted<Theme>('marquee:theme', 'dark', (t) => {
	if (browser) document.documentElement.dataset.theme = t;
});

export function toggleTheme() {
	theme.update((t) => (t === 'dark' ? 'light' : 'dark'));
}

export const sidebarCollapsed = persisted<boolean>('marquee:sidebarCollapsed', false);
export const filmMode = persisted<'list' | 'grid'>('marquee:filmMode', 'list');
