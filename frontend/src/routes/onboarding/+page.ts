import type { PageLoad } from './$types';
import { getOnboardingStatus } from '$lib/api/onboarding';
import type { OnboardingStatus } from '$lib/api/types';

export const load: PageLoad = async ({ fetch }) => {
	let status: OnboardingStatus | null = null;
	let error: string | null = null;
	try {
		status = await getOnboardingStatus(fetch);
	} catch (e) {
		error = e instanceof Error ? e.message : 'Could not load onboarding status';
	}
	return { status, error };
};
