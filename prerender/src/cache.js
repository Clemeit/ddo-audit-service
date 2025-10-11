const { createClient } = require('redis');
const config = require('./config');

class CacheManager {
  constructor() {
    this.client = null;
    this.connected = false;
  }

  async connect() {
    try {
      this.client = createClient({
        socket: {
          host: config.redis.host,
          port: config.redis.port,
        },
        password: config.redis.password,
      });

      this.client.on('error', (err) => {
        console.error('Redis connection error:', err.message);
        this.connected = false;
      });

      this.client.on('connect', () => {
        console.log(`Connected to Redis at ${config.redis.host}:${config.redis.port}`);
        this.connected = true;
      });

      await this.client.connect();
    } catch (error) {
      console.error('Failed to connect to Redis:', error.message);
      console.warn('Cache will be disabled. Service will continue without caching.');
      this.connected = false;
    }
  }

  /**
   * Normalize URL for consistent cache keys
   * - Remove fragments
   * - Sort query parameters
   */
  normalizeUrl(url) {
    try {
      const urlObj = new URL(url);
      urlObj.hash = ''; // Remove fragment
      urlObj.searchParams.sort(); // Sort query params
      return urlObj.toString();
    } catch (error) {
      return url; // Return as-is if parsing fails
    }
  }

  /**
   * Get cached content for a URL
   * Returns null if not found or cache is disabled
   */
  async get(url) {
    if (!this.connected || !this.client) {
      return null;
    }

    try {
      const key = `prerender:${this.normalizeUrl(url)}`;
      const cached = await this.client.get(key);
      
      if (!cached) {
        return null;
      }

      const data = JSON.parse(cached);
      return {
        html: data.html,
        statusCode: data.statusCode,
        headers: data.headers,
        timestamp: data.timestamp,
      };
    } catch (error) {
      console.error('Cache get error:', error.message);
      return null;
    }
  }

  /**
   * Store rendered content in cache
   * @param {string} url - The URL being cached
   * @param {string} html - The rendered HTML content
   * @param {number} statusCode - HTTP status code
   * @param {object} headers - Response headers
   * @param {number} ttl - Time to live in seconds (optional, uses config default)
   */
  async set(url, html, statusCode, headers = {}, ttl = config.cacheTTL) {
    if (!this.connected || !this.client) {
      return false;
    }

    try {
      const key = `prerender:${this.normalizeUrl(url)}`;
      const data = JSON.stringify({
        html,
        statusCode,
        headers,
        timestamp: Date.now(),
      });

      await this.client.setEx(key, ttl, data);
      return true;
    } catch (error) {
      console.error('Cache set error:', error.message);
      return false;
    }
  }

  /**
   * Clear cache for a specific URL
   */
  async clear(url) {
    if (!this.connected || !this.client) {
      return false;
    }

    try {
      const key = `prerender:${this.normalizeUrl(url)}`;
      await this.client.del(key);
      return true;
    } catch (error) {
      console.error('Cache clear error:', error.message);
      return false;
    }
  }

  /**
   * Close Redis connection
   */
  async disconnect() {
    if (this.client) {
      await this.client.quit();
      this.connected = false;
    }
  }
}

module.exports = new CacheManager();

