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
    // Decode once to handle inputs like .../about%3Ffoo=bar or fully-encoded /render/https%3A%2F%2F...
    let decoded = url;
    try {
      decoded = decodeURIComponent(url);
    } catch (_) {
      // If decoding fails (malformed %), keep original string
      decoded = url;
    }

    const urlObj = new URL(decoded);
    // Normalize early: strip all query params and fragments
    urlObj.search = '';
    urlObj.hash = '';

    // Drop everything after '&'. e.g. https://example.com/path&foo=bar -> https://example.com/path
    let normalizedPathname = urlObj.pathname;
    if (normalizedPathname.includes('&')) {
      const ampIndex = normalizedPathname.indexOf('&');
      normalizedPathname = normalizedPathname.substring(0, ampIndex);
    }
    urlObj.pathname = normalizedPathname;

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

    // Return the strictly normalized URL: origin + pathname (no query/hash)
    return { valid: true, url: `${urlObj.origin}${urlObj.pathname}` };
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
  // Expose normalized URL for easier verification in logs/clients
  try {
    res.setHeader('X-Prerender-Normalized-URL', validation.url);
  } catch (_) {
    // ignore header set failures
  }
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

/**
 * Identify well-known crawler by User-Agent
 * Returns a friendly name or null if not recognized.
 */
function identifyCrawler(userAgent = '') {
  if (!userAgent || typeof userAgent !== 'string') return null;

  const patterns = [
    [/googlebot/i, 'Googlebot'],
    [/googleother/i, 'GoogleOther'],
    [/googlereadaloud/i, 'Google Read Aloud'],
    [/apis-google/i, 'APIs-Google'],
    [/adsbot-google/i, 'AdsBot-Google'],
    [/mediapartners-google/i, 'Mediapartners-Google'],

    [/bingbot/i, 'Bingbot'],
    [/bingpreview/i, 'BingPreview'],

    [/slurp/i, 'Yahoo! Slurp'],
    [/duckduckbot/i, 'DuckDuckBot'],
    [/duckduckgo-favicons-bot/i, 'DuckDuckGo Favicons'],
    [/baiduspider/i, 'BaiduSpider'],
    [/yandex/i, 'YandexBot'],
    [/petalbot/i, 'PetalBot'],

    [/ahrefs(?:bot|siteaudit)/i, 'AhrefsBot'],
    [/semrush(?:bot)?/i, 'SemrushBot'],
    [/ccbot/i, 'Common Crawl (CCBot)'],
    [/mj12bot/i, 'Majestic (MJ12bot)'],
    [/blexbot/i, 'BLEXBot'],
    [/exabot/i, 'Exabot'],
    [/sogou/i, 'Sogou'],
    [/seznambot/i, 'SeznamBot'],
    [/(?:yeti|naverbot)/i, 'Naver (Yeti)'],
    [/ia_archiver/i, 'IA Archiver'],
    [/bytespider/i, 'ByteSpider (TikTok)'],

    [/facebookexternalhit/i, 'Facebook External Hit'],
    [/facebot/i, 'Facebook Bot'],
    [/twitterbot/i, 'Twitterbot'],
    [/linkedinbot/i, 'LinkedInBot'],
    [/embedly/i, 'Embedly'],
    [/quora\s+link\s+preview/i, 'Quora Link Preview'],
    [/showyoubot/i, 'Showyoubot'],
    [/outbrain/i, 'Outbrain'],
    [/pinterestbot/i, 'PinterestBot'],
    [/pinterest\/0\./i, 'Pinterest (Older UA)'],
    [/developers\.google\.com\/\+\/web\/snippet/i, 'Google+ Web Snippet'],
    [/slackbot/i, 'Slackbot'],
    [/slack-imgproxy/i, 'Slack Image Proxy'],
    [/vkshare/i, 'VK Share'],
    [/w3c_validator/i, 'W3C Validator'],
    [/redditbot/i, 'Redditbot'],
    [/applebot/i, 'Applebot'],
    [/whatsapp/i, 'WhatsApp'],
    [/flipboard/i, 'Flipboard'],
    [/tumblr/i, 'Tumblr'],
    [/bitlybot/i, 'BitlyBot'],
    [/skypeuripreview/i, 'Skype URI Preview'],
    [/nuzzel/i, 'Nuzzel'],
    [/discordbot/i, 'Discordbot'],
    [/google\s+page\s+speed/i, 'Google Page Speed'],
    [/qwantify/i, 'Qwantify'],
    [/bitrix\s+link\s+preview/i, 'Bitrix Link Preview'],
    [/xing-contenttabreceiver/i, 'Xing ContentTabReceiver'],
    [/chrome-lighthouse/i, 'Lighthouse'],
    [/telegrambot/i, 'TelegramBot'],

    // Project-specific test bots
    [/ddoaudit-test-bot/i, 'DDOAudit Test Bot'],
    [/staging-test-crawler/i, 'Staging Test Crawler'],
    [/prerender-test/i, 'Prerender Test'],

    // Legacy Moz
    [/rogerbot/i, 'Rogerbot'],
    [/dotbot/i, 'DotBot'],
  ];

  for (const [regex, name] of patterns) {
    if (regex.test(userAgent)) return name;
  }
  return null;
}

module.exports = {
  extractUrl,
  validateUrl,
  validateRequest,
  botUserAgents,
  identifyCrawler,
};

