const puppeteer = require('puppeteer');
const config = require('./config');

class PageRenderer {
  constructor() {
    this.browser = null;
  }

  async initialize() {
    try {
      console.log('Launching headless Chrome...');
      this.browser = await puppeteer.launch({
        headless: 'new',
        args: config.browserArgs,
      });
      console.log('Chrome launched successfully');
    } catch (error) {
      console.error('Failed to launch browser:', error.message);
      throw error;
    }
  }

  /**
   * Render a page and return the HTML content with metadata
   * @param {string} url - The URL to render
   * @returns {object} - { html, statusCode, headers, redirected, finalUrl }
   */
  async render(url) {
    if (!this.browser) {
      throw new Error('Browser not initialized. Call initialize() first.');
    }

    let page = null;

    try {
      page = await this.browser.newPage();

      // Set a realistic user agent
      await page.setUserAgent(
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
      );

      // Set viewport
      await page.setViewport({ width: 1920, height: 1080 });

      // Track response details
      let statusCode = 200;
      let responseHeaders = {};
      let redirected = false;
      let finalUrl = url;

      // Listen for the main response
      page.on('response', (response) => {
        if (response.url() === url || response.url() === finalUrl) {
          statusCode = response.status();
          responseHeaders = response.headers();
        }
      });

      // Navigate to the URL
      const response = await page.goto(url, {
        waitUntil: config.waitUntil,
        timeout: config.pageTimeout,
      });

      // Check if redirected
      const finalResponse = response || (await page.waitForResponse(() => true));
      if (finalResponse) {
        statusCode = finalResponse.status();
        finalUrl = finalResponse.url();
        redirected = finalUrl !== url;
        responseHeaders = finalResponse.headers();
      }

      // Wait a bit more for any remaining JavaScript execution
      await page.evaluate(() => new Promise(resolve => setTimeout(resolve, 500)));

      // Inspect meta tags to optionally override status or set headers for crawlers
      const metaInfo = await page.evaluate(() => {
        const getMeta = (name) => {
          const el = document.querySelector(`meta[name="${name}"]`);
          return el ? el.getAttribute('content') : null;
        };
        return {
          prerenderStatusCode: getMeta('prerender-status-code'),
          robots: getMeta('robots'),
        };
      });

      // Apply overrides from meta tags if present
      if (metaInfo && metaInfo.prerenderStatusCode) {
        const parsed = parseInt(metaInfo.prerenderStatusCode, 10);
        if (!Number.isNaN(parsed) && parsed >= 100 && parsed <= 599) {
          statusCode = parsed;
        }
      }

      if (metaInfo && metaInfo.robots) {
        // Expose robots directives in a response header for crawlers
        responseHeaders = {
          ...responseHeaders,
          'x-robots-tag': metaInfo.robots,
        };
      }

      // Get the rendered HTML
      const html = await page.content();

      return {
        html,
        statusCode,
        headers: responseHeaders,
        redirected,
        finalUrl,
      };
    } catch (error) {
      // Handle specific error types
      if (error.name === 'TimeoutError') {
        console.error(`Timeout rendering ${url}`);
        return {
          html: `<html><body><h1>Timeout Error</h1><p>The page took too long to load.</p></body></html>`,
          statusCode: 504,
          headers: {},
          redirected: false,
          finalUrl: url,
        };
      }

      // Handle navigation errors (DNS, connection refused, etc.)
      if (error.message.includes('net::ERR_') || error.message.includes('NS_ERROR_')) {
        console.error(`Navigation error for ${url}:`, error.message);
        return {
          html: `<html><body><h1>Connection Error</h1><p>Could not connect to the requested page.</p></body></html>`,
          statusCode: 502,
          headers: {},
          redirected: false,
          finalUrl: url,
        };
      }

      console.error(`Error rendering ${url}:`, error.message);
      throw error;
    } finally {
      if (page) {
        await page.close();
      }
    }
  }

  /**
   * Close the browser instance
   */
  async close() {
    if (this.browser) {
      await this.browser.close();
      this.browser = null;
      console.log('Browser closed');
    }
  }
}

module.exports = new PageRenderer();

