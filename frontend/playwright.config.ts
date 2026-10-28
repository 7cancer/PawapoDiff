import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: process.env.CI ? [["list"], ["github"]] : "list",
  use: {
    baseURL: "http://127.0.0.1:8784",
    launchOptions: {
      executablePath: process.env.PAWAPO_CHROMIUM_PATH || undefined,
    },
  },
  webServer: {
    command:
      process.platform === "win32"
        ? "..\\.venv\\Scripts\\python.exe ..\\scripts\\frontend-test-server.py"
        : "../.venv/bin/python ../scripts/frontend-test-server.py",
    url: "http://127.0.0.1:8784",
    reuseExistingServer: false,
    timeout: 30000,
  },
});
