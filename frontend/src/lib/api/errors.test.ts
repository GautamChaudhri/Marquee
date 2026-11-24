import { describe, expect, it } from 'vitest';
import { detailOf, messageFor } from './errors';

describe('error messages', () => {
	it('shows the backend detail as a sentence', () => {
		expect(messageFor(409, { detail: 'the run has no pick' })).toBe('The run has no pick.');
		expect(messageFor(404, { detail: 'movie not found' })).toBe('Movie not found.');
		expect(messageFor(409, { detail: 'Done already.' })).toBe('Done already.');
	});

	it('turns a validation list into fields and what is wrong with them', () => {
		const error = {
			detail: [
				{ loc: ['query', 'width'], msg: 'Input should be 160, 320 or 640', type: 'enum' },
				{ loc: ['body'], msg: 'Field required' }
			]
		};
		expect(messageFor(422, error)).toBe('Width: Input should be 160, 320 or 640; Field required.');
	});

	it('describes the status when there is no detail, and never shows raw JSON', () => {
		expect(messageFor(0, new TypeError('Failed to fetch'))).toMatch(/not reachable/);
		expect(messageFor(502)).toBe('The API is not reachable.');
		expect(messageFor(504, 'gateway text')).toBe('The API did not answer in time.');
		expect(messageFor(500, { error: 'x' })).toBe('The API failed (status 500).');
		expect(messageFor(418)).toBe('Something went wrong (status 418).');
		for (const status of [0, 400, 401, 403, 404, 409, 413, 422, 429, 500, 502, 503, 504]) {
			expect(messageFor(status, { detail: '' })).not.toMatch(/[{}[\]"]/);
		}
	});

	it('finds no detail in what has none', () => {
		for (const value of [null, undefined, 'text', 3, {}, { detail: 7 }, { detail: [] }]) {
			expect(detailOf(value)).toBeNull();
		}
	});
});
