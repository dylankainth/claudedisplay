#!/usr/bin/env node
/**
 * Claude Usage API Server
 * Runs Claude Code /usage and exposes as JSON API
 * Runs inside Docker container
 */

const express = require('express');
const { execSync } = require('child_process');
const app = express();

const PORT = 5000;

// Poll every 15 minutes (900,000 ms) instead of 30 seconds to prevent rate-limiting
const POLL_INTERVAL = 15 * 60 * 1000; 
let isPolling = false;

// Holds the last successfully parsed data to survive CLI hiccups
let lastGood = {
  session: "--",
  weekly: "--",
  sessionReset: "--",
  weeklyReset: "--",
  timestamp: null
};

// ============ Helper: Calculate Time Until Reset ============
const calculateTimeUntilReset = (resetStr) => {
  if (!resetStr || resetStr === "--") return "--";
  
  try {
    const now = new Date();
    const currentYear = now.getFullYear();

    const match = resetStr.match(/(\w+)\s+(\d+),\s+(\d+):?(\d*)([ap]m)/i);
    if (!match) return resetStr;

    const [, month, day, hour, minute, ampm] = match;

    let hourNum = parseInt(hour);
    if (ampm.toLowerCase() === 'pm' && hourNum !== 12) hourNum += 12;
    if (ampm.toLowerCase() === 'am' && hourNum === 12) hourNum = 0;
    const minNum = minute ? parseInt(minute) : 0;

    const resetDate = new Date(currentYear,
      new Date(`${month} 1`).getMonth(),
      parseInt(day),
      hourNum,
      minNum,
      0
    );

    if (resetDate < now) {
      if (resetDate.getMonth() === 11) { 
        resetDate.setFullYear(currentYear + 1);
        resetDate.setMonth(0); 
      } else {
        resetDate.setMonth(resetDate.getMonth() + 1);
      }
    }

    const diffMs = resetDate - now;
    if (diffMs < 0) return "0h 0m";
    
    const hours = Math.floor(diffMs / (1000 * 60 * 60));
    const minutes = Math.floor((diffMs % (1000 * 60 * 60)) / (1000 * 60));

    return `${hours}h ${minutes}m`;
  } catch (e) {
    console.error(`Failed to parse reset time: ${resetStr}`, e);
    return resetStr;
  }
};

// ============ Claude Usage Polling ============
function pollClaudeUsage() {
  if (isPolling) {
    console.log("Polling already in progress, skipping...");
    return;
  }
  isPolling = true;

  try {
    console.log(`[${new Date().toISOString()}] Initiating Claude /usage CLI poll...`);
    const output = execSync('claude -p /usage', {
      encoding: 'utf-8',
      timeout: 30000,
      stdio: ['pipe', 'pipe', 'pipe']
    });

    console.log(`[${new Date().toISOString()}] Claude /usage output:\n${output}\n`);

    // Parse output with regex
    const sessionMatch = output.match(/Current session:\s+(\d+)%.*?resets\s+([^(]+)/);
    const weeklyMatch = output.match(/Current week.*?:\s+(\d+)%.*?resets\s+([^(]+)/);

    // If we found valid data, update our "Last Known Good" state
    if (sessionMatch || weeklyMatch) {
      if (sessionMatch) {
        lastGood.session = `${sessionMatch[1]}%`;
        lastGood.sessionReset = sessionMatch[2].trim();
      }
      if (weeklyMatch) {
        lastGood.weekly = `${weeklyMatch[1]}%`;
        lastGood.weeklyReset = weeklyMatch[2].trim();
      }
      lastGood.timestamp = new Date().toISOString();
      console.log(`✓ Cached new usage data: session=${lastGood.session}, weekly=${lastGood.weekly}`);
    } else {
      console.log(`⚠️ CLI returned incomplete output. Retaining last known values.`);
    }
  } catch (error) {
    console.error(`✗ Failed to call Claude: ${error.message}`);
  } finally {
    isPolling = false;
  }
}

// Generate the JSON payload dynamically on-request
function getUsageData() {
  const sessionResetCountdown = calculateTimeUntilReset(lastGood.sessionReset);
  const weeklyResetCountdown = calculateTimeUntilReset(lastGood.weeklyReset);

  // Determine status dynamically
  let status = "ok";
  try {
    const s = parseInt(lastGood.session.replace('%', ''));
    const w = parseInt(lastGood.weekly.replace('%', ''));
    if (s > 90 || w > 90) {
      status = "crit";
    } else if (s > 70 || w > 70) {
      status = "warn";
    }
  } catch (e) {
    status = "unk";
  }

  return {
    session: lastGood.session,
    weekly: lastGood.weekly,
    sessionReset: lastGood.sessionReset,
    weeklyReset: lastGood.weeklyReset,
    sessionResetIn: sessionResetCountdown,
    weeklyResetIn: weeklyResetCountdown,
    status,
    timestamp: lastGood.timestamp || new Date().toISOString(),
    isPolling
  };
}

// Set relaxed interval
setInterval(pollClaudeUsage, POLL_INTERVAL);

// Initial poll on startup
console.log("Starting Claude Usage API Server");
pollClaudeUsage();

// ============ Routes ============
app.get('/usage', (req, res) => {
  res.json(getUsageData());
});

// Manual refresh trigger
app.post('/refresh', (req, res) => {
  if (isPolling) {
    return res.status(429).json({ error: "Poll already running" });
  }
  // Run async so we don't block the HTTP response
  setTimeout(pollClaudeUsage, 10);
  res.json({ status: "refresh_initiated" });
});

app.get('/health', (req, res) => {
  res.json({
    status: lastGood.timestamp ? "up" : "degraded",
    timestamp: new Date().toISOString(),
    lastPoll: lastGood.timestamp,
    isPolling
  });
});

app.get('/', (req, res) => {
  res.json({
    service: "Claude Usage API",
    version: "1.1",
    endpoints: {
      "/usage": "Get session/weekly usage percentages (cached, dynamic countdowns)",
      "/refresh": "POST to manually force CLI status poll",
      "/health": "Health check"
    },
    current: getUsageData()
  });
});

// ============ Start Server ============
app.listen(PORT, '0.0.0.0', () => {
  console.log(`✓ API Server listening on 0.0.0.0:${PORT}`);
  console.log(`   GET http://localhost:${PORT}/usage`);
  console.log(`   POST http://localhost:${PORT}/refresh`);
});
