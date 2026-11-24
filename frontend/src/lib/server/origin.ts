/** A request that changes something must come from this site's own pages. */
const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

export function isSafeMethod(method: string): boolean {
	return SAFE_METHODS.has(method.toUpperCase());
}

/** Whether the browser says the request came from a page of `url`'s origin. A request with
 *  neither an Origin nor a Sec-Fetch-Site header (not a browser) is not same-origin. */
export function isSameOrigin(request: Request, url: URL): boolean {
	const site = request.headers.get('sec-fetch-site');
	if (site !== null && site !== 'same-origin') return false;
	const origin = request.headers.get('origin');
	if (origin !== null) return origin === url.origin;
	return site === 'same-origin';
}
