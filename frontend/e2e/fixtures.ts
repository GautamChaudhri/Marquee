import { expect, test as base } from '@playwright/test';

/** Every end-to-end test fails if the browser reports a Content-Security-Policy violation
 *  on any page it opened: a policy that is too tight blocks a script or a style silently,
 *  and the page can still look right. The violations are collected from the page itself
 *  (the `securitypolicyviolation` event, registered before any page script runs) and from
 *  the console, where the browser reports each one too. */
export const test = base.extend<{ cspViolations: string[] }>({
	cspViolations: [
		async ({ page }, use) => {
			const violations: string[] = [];
			await page.exposeFunction('__marqueeCspViolation', (line: string) => {
				violations.push(line);
			});
			await page.addInitScript(() => {
				document.addEventListener('securitypolicyviolation', (event) => {
					const report = (window as unknown as Record<string, (line: string) => void>)
						.__marqueeCspViolation;
					report?.(
						`${event.violatedDirective} blocked ${event.blockedURI || 'inline'} on ${location.pathname}`
					);
				});
			});
			page.on('console', (message) => {
				if (message.text().includes('Content Security Policy')) {
					violations.push(`console: ${message.text()}`);
				}
			});
			await use(violations);
			expect(violations, 'Content-Security-Policy violations').toEqual([]);
		},
		{ auto: true }
	]
});

export { expect };
