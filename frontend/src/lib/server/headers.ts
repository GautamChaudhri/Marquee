/** The headers every response of the web server carries. */
const SECURITY_HEADERS: Record<string, string> = {
	'x-content-type-options': 'nosniff',
	'x-frame-options': 'DENY',
	'referrer-policy': 'same-origin'
};

/** A browser that has seen this over https uses https for the site for a year. */
export const HSTS = 'max-age=31536000';

/** The three headers, and HSTS when the site's own address (ORIGIN, which `url` is built
 *  from) is https: the owner's reverse proxy serves it. Over plain HTTP a browser ignores
 *  HSTS, so it is not sent. */
export function securityHeaders(url: URL): Record<string, string> {
	return url.protocol === 'https:'
		? { ...SECURITY_HEADERS, 'strict-transport-security': HSTS }
		: { ...SECURITY_HEADERS };
}
