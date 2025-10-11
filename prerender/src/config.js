require('dotenv').config();

const config = {
  // Server configuration
  port: parseInt(process.env.PORT || '3000', 10),
  
  // Redis configuration
  redis: {
    host: process.env.REDIS_HOST || 'redis',
    port: parseInt(process.env.REDIS_PORT || '6379', 10),
    password: process.env.REDIS_PASSWORD || undefined,
  },
  
  // Cache configuration
  cacheTTL: parseInt(process.env.CACHE_TTL || '86400', 10), // Default: 24 hours in seconds
  
  // Rendering configuration
  pageTimeout: parseInt(process.env.PAGE_TIMEOUT || '30000', 10), // Default: 30 seconds
  waitUntil: process.env.WAIT_UNTIL || 'networkidle0', // Options: load, domcontentloaded, networkidle0, networkidle2
  
  // Optional domain whitelist (comma-separated)
  allowedDomains: process.env.ALLOWED_DOMAINS 
    ? process.env.ALLOWED_DOMAINS.split(',').map(d => d.trim())
    : null,
  
  // Browser configuration
  browserArgs: [
    '--no-sandbox',
    '--disable-setuid-sandbox',
    '--disable-dev-shm-usage',
    '--disable-accelerated-2d-canvas',
    '--no-first-run',
    '--no-zygote',
    '--disable-gpu'
  ],
};

module.exports = config;

