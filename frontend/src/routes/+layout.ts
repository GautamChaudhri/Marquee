// The pages render in the browser and read the API through this site's own /api. The
// server still checks the session for every page request (hooks.server.ts) before it sends
// the page, so nothing is shown to a browser that is not signed in.
export const ssr = false;
