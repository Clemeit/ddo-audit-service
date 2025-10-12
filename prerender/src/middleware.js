const config = require('./config');

/**
 * Extract the target URL from the request
 * Supports multiple formats:
 * - Query parameter: ?url=https://example.com
 * - Path format: /render/https://example.com
 * - Custom header: X-Prerender-URL
 */
function extractUrl(req) {
  // Check query parameter
  if (req.query.url) {
    return req.query.url;
  }

  // Check custom header
  if (req.headers['x-prerender-url']) {
    return req.headers['x-prerender-url'];
  }

  // Check path format (e.g., /render/https://example.com)
  const pathMatch = req.path.match(/^\/render\/(.+)$/);
  if (pathMatch) {
    return pathMatch[1];
  }

  // Try to extract from path (e.g., /https://example.com)
  if (req.path.length > 1) {
    const possibleUrl = req.path.substring(1);
    if (possibleUrl.startsWith('http://') || possibleUrl.startsWith('https://')) {
      return possibleUrl;
    }
  }

  return null;
}

/**
 * Validate URL format and optional domain whitelist
 */
function validateUrl(url) {
  if (!url) {
    return { valid: false, error: 'No URL provided' };
  }

  // Check if it's a valid URL
  try {
    const urlObj = new URL(url);
    // Normalize early: strip all query params and fragments
    urlObj.search = '';
    urlObj.hash = '';

    // Only allow http and https protocols
    if (!['http:', 'https:'].includes(urlObj.protocol)) {
      return { valid: false, error: 'Only HTTP and HTTPS protocols are allowed' };
    }

    // Check against domain whitelist if configured
    if (config.allowedDomains && config.allowedDomains.length > 0) {
      const hostname = urlObj.hostname;
      const isAllowed = config.allowedDomains.some(domain => {
        // Exact match or subdomain match
        return hostname === domain || hostname.endsWith(`.${domain}`);
      });

      if (!isAllowed) {
        return {
          valid: false,
          error: `Domain ${hostname} is not in the allowed domains list`
        };
      }
    }

    // Return the normalized URL string
    return { valid: true, url: urlObj.toString() };
  } catch (error) {
    return { valid: false, error: 'Invalid URL format' };
  }
}

/**
 * Middleware to validate incoming requests
 */
function validateRequest(req, res, next) {
  const url = extractUrl(req);
  const validation = validateUrl(url);

  if (!validation.valid) {
    return res.status(400).json({
      error: validation.error,
      hint: 'Provide URL via ?url=https://example.com or X-Prerender-URL header',
    });
  }

  // Log when URL normalization has changed the original input
  if (url !== validation.url) {
    try {
      console.log(`[${new Date().toISOString()}] Normalized URL: ${url} -> ${validation.url}`);
    } catch (e) {
      // no-op if logging fails
    }
  }

  // Attach validated URL to request
  req.targetUrl = validation.url;
  next();
}

/**
 * Common bot user agents for reference
 * (Not used for filtering in this service, as nginx should handle that)
 */
const botUserAgents = [
  'googlebot',
  'bingbot',
  'slurp', // Yahoo
  'duckduckbot',
  'baiduspider',
  'yandexbot',
  'facebookexternalhit',
  'twitterbot',
  'linkedinbot',
  'whatsapp',
  'telegrambot',
];

module.exports = {
  extractUrl,
  validateUrl,
  validateRequest,
  botUserAgents,
};

