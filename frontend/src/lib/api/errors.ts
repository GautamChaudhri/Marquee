/** The one place a failed API call becomes a message a person can read. No page shows a raw
 *  JSON error: a backend `detail` is shown as a sentence, a validation list as the fields and
 *  what is wrong with them, and anything else as a plain description of the status. */

const BY_STATUS: Record<number, string> = {
	0: 'The web server is not reachable. Check the connection and try again.',
	400: 'The request was not understood.',
	401: 'Your session has ended. Sign in again.',
	403: 'That was refused.',
	404: 'Not found.',
	409: 'That cannot be done right now.',
	413: 'The request is too large.',
	422: 'The request was not valid.',
	429: 'Too many requests. Wait a moment and try again.',
	502: 'The API is not reachable.',
	503: 'The API is not ready.',
	504: 'The API did not answer in time.'
};

function sentence(text: string): string {
	const trimmed = text.trim().replace(/\s+/g, ' ');
	if (!trimmed) return trimmed;
	const capital = trimmed[0]!.toUpperCase() + trimmed.slice(1);
	return /[.!?]$/.test(capital) ? capital : `${capital}.`;
}

interface ValidationItem {
	loc?: unknown;
	msg?: unknown;
}

function fieldOf(item: ValidationItem): string | null {
	if (!Array.isArray(item.loc)) return null;
	const parts = item.loc.filter((part) => !['body', 'query', 'path'].includes(String(part)));
	return parts.length ? parts.join('.') : null;
}

/** The backend's `detail`, as readable text, or null when there is none to show. */
export function detailOf(error: unknown): string | null {
	if (!error || typeof error !== 'object' || !('detail' in error)) return null;
	const detail = (error as { detail: unknown }).detail;
	if (typeof detail === 'string' && detail.trim()) return sentence(detail);
	if (Array.isArray(detail)) {
		const lines = detail
			.filter((item): item is ValidationItem => !!item && typeof item === 'object')
			.map((item) => {
				const message = typeof item.msg === 'string' ? item.msg : 'is not valid';
				const field = fieldOf(item);
				return field ? `${field}: ${message}` : message;
			});
		return lines.length ? sentence(lines.join('; ')) : null;
	}
	return null;
}

/** A message for a failed call: the backend's detail when it gave one, else the status. */
export function messageFor(status: number, error?: unknown): string {
	const detail = detailOf(error);
	if (detail) return detail;
	if (status in BY_STATUS) return BY_STATUS[status]!;
	if (status >= 500) return `The API failed (status ${status}).`;
	return `Something went wrong (status ${status}).`;
}
