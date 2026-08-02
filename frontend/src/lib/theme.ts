/** Theme + persisted-UI stores. The `[data-theme]` attribute lives on <html>;
 *  an inline script in app.html sets it pre-render to avoid a flash. */
import { browser } from '$app/environment';
import { persisted } from '$lib/persisted';

export type Theme = 'dark' | 'light';
export type LibraryPosterSize = 'small' | 'medium' | 'large';

export const theme = persisted<Theme>('marquee:theme', 'dark', (t) => {
	if (browser) document.documentElement.dataset.theme = t;
});

export function toggleTheme() {
	theme.update((t) => (t === 'dark' ? 'light' : 'dark'));
}

export const sidebarCollapsed = persisted<boolean>('marquee:sidebarCollapsed', false);
export const filmMode = persisted<'list' | 'grid'>('marquee:filmMode', 'list');
export const televisionMode = persisted<'list' | 'grid'>('marquee:televisionMode', 'grid');
export const libraryPosterSize = persisted<LibraryPosterSize>(
	'marquee:libraryPosterSize',
	'medium'
);
