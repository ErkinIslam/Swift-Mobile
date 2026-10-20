// Browser tests against scripts/dev_server.py (fake email, vercel.json headers).
// Run from this folder: npm install && npm run install-browsers && npm test
const { defineConfig, devices } = require("@playwright/test");

const PORT = 8787;

module.exports = defineConfig({
  testDir: ".",
  fullyParallel: false, // the dev server's outbox is shared
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    timezoneId: "America/New_York", // the business's time zone: date and time-slot rules depend on it
    locale: "en-US",
    trace: "retain-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "iphone", use: { ...devices["iPhone 13"] } },
    { name: "android", use: { ...devices["Pixel 7"] } },
  ],
  webServer: {
    command: `python3 ../../scripts/dev_server.py --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}/`,
    env: { DEV_SERVER_QUIET: "1", PYTHONDONTWRITEBYTECODE: "1" },
    reuseExistingServer: false,
  },
});
