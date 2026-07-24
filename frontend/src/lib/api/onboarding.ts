import { apiGet, apiSend, type Fetch } from './client';
import type { components } from './generated/openapi';

type Schemas = components['schemas'];

export type OnboardingStatus = Schemas['OnboardingStatusResponse'];
export type OnboardingStartResult = Schemas['OnboardingStartResponse'];
export type OnboardingReview = Schemas['OnboardingReviewResponse'];
export type OnboardingDecision = Schemas['OnboardingDecisionResponse'];
export type OnboardingCompletionResult = Schemas['OnboardingCompletionResponse'];
export type OnboardingDecisionIntent = Schemas['ChoosePosterRequest'];

/** Cold-start onboarding status: canonical evidence and profile-build progress. */
export function getOnboardingStatus(
	fetchFn: Fetch,
	signal?: AbortSignal
): Promise<OnboardingStatus> {
	return apiGet<OnboardingStatus>(fetchFn, '/onboarding/status', undefined, signal);
}

/** Submit profile-independent analysis for the next unconfirmed movie. */
export function startOnboarding(fetchFn: Fetch): Promise<OnboardingStartResult> {
	return apiSend<OnboardingStartResult>(fetchFn, 'POST', '/onboarding/start', {});
}

/** Load a neutral candidate review whose membership and evidence stay server-owned. */
export function getOnboardingReview(
	fetchFn: Fetch,
	runId: string,
	signal?: AbortSignal
): Promise<OnboardingReview> {
	return apiGet<OnboardingReview>(
		fetchFn,
		`/onboarding/runs/${encodeURIComponent(runId)}/review`,
		undefined,
		signal
	);
}

/** Submit one canonical review choice; the browser contributes intent only. */
export function chooseOnboardingCandidate(
	fetchFn: Fetch,
	intent: OnboardingDecisionIntent
): Promise<OnboardingDecision> {
	return apiSend<OnboardingDecision>(fetchFn, 'POST', '/onboarding/choose', intent);
}

/** Record explicit dislike without triggering any poster deployment. */
export function hateOnboardingCandidate(
	fetchFn: Fetch,
	intent: OnboardingDecisionIntent
): Promise<OnboardingDecision> {
	return apiSend<OnboardingDecision>(fetchFn, 'POST', '/onboarding/hate', intent);
}

/** Build both native taste profiles from the exact eligible evidence revision. */
export function completeOnboarding(fetchFn: Fetch): Promise<OnboardingCompletionResult> {
	return apiSend<OnboardingCompletionResult>(fetchFn, 'POST', '/onboarding/complete', {});
}
