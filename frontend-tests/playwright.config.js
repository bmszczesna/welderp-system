const path = require('node:path');
const {defineConfig, devices} = require('@playwright/test');

const externalBaseUrl = process.env.WELDERP_BASE_URL;
const python = process.env.PYTHON || (process.platform === 'win32'
    ? path.resolve(__dirname, '../.venv/Scripts/python.exe')
    : 'python');

module.exports = defineConfig({
    testDir: './tests',
    fullyParallel: true,
    forbidOnly: Boolean(process.env.CI),
    retries: process.env.CI ? 1 : 0,
    reporter: process.env.CI ? 'line' : 'list',
    use: {
        baseURL: externalBaseUrl || 'http://127.0.0.1:8000',
        trace: 'retain-on-failure',
        ...devices['Desktop Chrome'],
    },
    ...(!externalBaseUrl ? {
        webServer: {
            command: `"${python}" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000`,
            cwd: path.resolve(__dirname, '..'),
            url: 'http://127.0.0.1:8000/health',
            reuseExistingServer: !process.env.CI,
            timeout: 120_000,
        },
    } : {}),
});
