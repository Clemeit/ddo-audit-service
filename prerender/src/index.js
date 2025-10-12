const express = require('express');
const config = require('./config');
const cache = require('./cache');
const renderer = require('./renderer');
const { validateRequest } = require('./middleware');

const app = express();

// Health check endpoint
app.get('/health', (req, res) => {
  res.json({
    status: 'ok',
    cache: cache.connected ? 'connected' : 'disabled',
    timestamp: new Date().toISOString(),
  });
});

// Main prerender endpoint
app.get('*', validateRequest, async (req, res) => {
  const targetUrl = req.targetUrl;
  const startTime = Date.now();

  console.log(`[${new Date().toISOString()}] Rendering: ${targetUrl}`);

  try {
    // Try to get from cache first
    const cached = await cache.get(targetUrl);
    const cacheLookupTime = Date.now() - startTime;

    if (cached) {
      const age = Math.floor((Date.now() - cached.timestamp) / 1000);
      console.log(`Cache HIT for ${targetUrl} (age: ${age}s, lookup: ${cacheLookupTime}ms)`);

      res.setHeader('X-Prerender-Cache', 'HIT');
      res.setHeader('X-Prerender-Cache-Age', age.toString());
      res.status(cached.statusCode);

      // Forward important headers from cache
      if (cached.headers['content-type']) {
        res.setHeader('Content-Type', cached.headers['content-type']);
      }

      return res.send(cached.html);
    }

    console.log(`Cache MISS for ${targetUrl} - rendering...`);

    // Render the page
    const result = await renderer.render(targetUrl);
    const renderTime = Date.now() - startTime;

    console.log(`Rendered ${targetUrl} in ${renderTime}ms (status: ${result.statusCode})`);

    // Store in cache (even error pages to avoid hammering failing sites)
    await cache.set(targetUrl, result.html, result.statusCode, result.headers);

    // Set response headers
    res.setHeader('X-Prerender-Cache', 'MISS');
    res.setHeader('X-Prerender-Render-Time', renderTime.toString());
    res.status(result.statusCode);

    // Forward important headers
    if (result.headers['content-type']) {
      res.setHeader('Content-Type', result.headers['content-type']);
    } else {
      res.setHeader('Content-Type', 'text/html; charset=utf-8');
    }

    // Add redirect information if applicable
    if (result.redirected) {
      res.setHeader('X-Prerender-Redirected', 'true');
      res.setHeader('X-Prerender-Final-URL', result.finalUrl);
    }

    res.send(result.html);
  } catch (error) {
    console.error(`Error processing ${targetUrl}:`, error.message);

    res.status(500).json({
      error: 'Failed to render page',
      message: error.message,
      url: targetUrl,
    });
  }
});

// 404 handler
app.use((req, res) => {
  res.status(404).json({
    error: 'Not found',
    hint: 'Use /?url=https://example.com or /render/https://example.com',
  });
});

// Error handler
app.use((err, req, res, next) => {
  console.error('Unhandled error:', err);
  res.status(500).json({
    error: 'Internal server error',
    message: err.message,
  });
});

// Initialize and start server
async function start() {
  try {
    console.log('Starting prerender service...');

    // Connect to Redis
    await cache.connect();

    // Initialize browser
    await renderer.initialize();

    // Start HTTP server
    app.listen(config.port, () => {
      console.log(`\n🚀 Prerender service running on port ${config.port}`);
      console.log(`   Health check: http://localhost:${config.port}/health`);
      console.log(`   Render URL: http://localhost:${config.port}/?url=YOUR_URL\n`);
    });
  } catch (error) {
    console.error('Failed to start service:', error);
    process.exit(1);
  }
}

// Graceful shutdown
process.on('SIGTERM', async () => {
  console.log('SIGTERM received, shutting down gracefully...');
  await renderer.close();
  await cache.disconnect();
  process.exit(0);
});

process.on('SIGINT', async () => {
  console.log('SIGINT received, shutting down gracefully...');
  await renderer.close();
  await cache.disconnect();
  process.exit(0);
});

// Start the service
start();

