import { describe, expect, it } from 'vitest';
import { compareBySortTitle, sortTitle } from './sort-title';

// A pure-logic smoke test for the `server` (node) Vitest project.
describe('sortTitle', () => {
	it('moves a leading article to the end', () => {
		expect(sortTitle('The Dark Knight')).toBe('Dark Knight, The');
		expect(sortTitle('A Beautiful Mind')).toBe('Beautiful Mind, A');
	});

	it('leaves a title without a leading article unchanged', () => {
		expect(sortTitle('Inception')).toBe('Inception');
	});

	it('orders titles ignoring leading articles', () => {
		const titles = ['The Zone', 'Apple', 'An Ocean'];
		expect([...titles].sort(compareBySortTitle)).toEqual(['Apple', 'An Ocean', 'The Zone']);
	});
});
