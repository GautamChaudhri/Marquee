import { fireEvent, render, screen } from '@testing-library/svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { RuntimeSettings } from '$lib/api/types';
import IntegrationCard from './IntegrationCard.svelte';

vi.mock('$env/dynamic/public', () => ({ env: { PUBLIC_USE_MOCKS: 'false' } }));

function settings(): RuntimeSettings {
	return {
		configuration_version: 7,
		values: { RADARR_URL: 'http://radarr:7878' },
		secrets: {
			RADARR_API_KEY: {
				configured: true,
				source: 'managed',
				generation: 3,
				updated_at: null
			}
		},
		secret_store: { writable: true, reason: null }
	} as unknown as RuntimeSettings;
}

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('IntegrationCard secure transport gate', () => {
	it('fails closed and never constructs a credential request in an insecure context', async () => {
		const fetchSpy = vi.fn();
		vi.stubGlobal('fetch', fetchSpy);
		render(IntegrationCard, {
			provider: 'radarr',
			settings: settings(),
			secureContext: false,
			onSettings: vi.fn()
		});

		const credential = screen.getByLabelText('API key') as HTMLInputElement;
		const test = screen.getByRole('button', { name: 'Test connection' });
		const save = screen.getByRole('button', { name: 'Test & save' });
		const clear = screen.getByRole('button', { name: 'Clear credential' });
		expect(credential).toBeDisabled();
		expect(test).toBeDisabled();
		expect(save).toBeDisabled();
		expect(clear).toBeDisabled();
		expect(
			screen.getByText(/Credential controls are disabled until this page is opened/)
		).toBeVisible();

		// Exercise the handler guard independently from disabled markup.
		credential.removeAttribute('disabled');
		await fireEvent.input(credential, { target: { value: 'candidate-secret' } });
		test.removeAttribute('disabled');
		await fireEvent.click(test);

		// The card also reads a library count on mount, which carries no credential.
		// What must never happen is a request to an integrations endpoint, since
		// those are the ones that would put a secret on an insecure wire.
		const requested = fetchSpy.mock.calls.map(([input]) => String(input));
		expect(requested.filter((url) => url.includes('/settings/integrations'))).toEqual([]);
		expect(requested.join(' ')).not.toContain('candidate-secret');
		expect(credential).toHaveValue('');
	});
});
