import { spawn, type ChildProcess } from 'node:child_process';
import { join } from 'node:path';

import { expect, test } from '@playwright/test';

// The wallet as a person gets it on a computer: a wallet package served by the start script at its one address.
// Without FORT_WALLET_FOLDER that is this checkout's build, served by scripts/start_wallet.py. With it, it is an
// unzipped download, started by the script in that folder as its README says.
const ADDRESS = 'http://127.0.0.1:8791/';
const DOWNLOAD = process.env['FORT_WALLET_FOLDER'];
const COMMAND = DOWNLOAD
    ? [join(DOWNLOAD, 'start-wallet.py'), '--no-browser']
    : ['scripts/start_wallet.py', '--root', 'dist/runtime', '--no-browser'];
let script: ChildProcess | undefined;

test.beforeAll(async () => {
    const started = spawn('python3', COMMAND, { stdio: ['ignore', 'pipe', 'pipe'] });
    script = started;
    await new Promise<void>((resolve, reject) => {
        // Only this line on standard output means the script serves. Its refusals name the address too, on
        // standard error, and what they say is kept for the failure message.
        let said = '';
        let warned = '';
        const timer = setTimeout(
            () => reject(new Error(`the start script did not start in 15 s: ${said}${warned}`)),
            15_000,
        );
        started.stdout?.on('data', (chunk: Buffer) => {
            said += chunk.toString();
            if (said.includes(`The wallet is at ${ADDRESS}`)) {
                clearTimeout(timer);
                resolve();
            }
        });
        started.stderr?.on('data', (chunk: Buffer) => {
            warned += chunk.toString();
        });
        started.once('error', (error) => {
            clearTimeout(timer);
            reject(new Error(`the start script could not be started (python3 ${COMMAND.join(' ')}): ${error.message}`));
        });
        started.once('close', (code) => {
            clearTimeout(timer);
            reject(new Error(`the start script stopped with code ${code}: ${said}${warned}`));
        });
    });
});

test.afterAll(async () => {
    // Wait until the script has exited, so the port is free for whatever runs next.
    const running = script;
    if (running?.pid === undefined || running.exitCode !== null || running.signalCode !== null) {
        return;
    }
    const exited = new Promise<void>((resolve) => running.once('exit', () => resolve()));
    running.kill();
    await exited;
});

test('the wallet opens from the start script, and a wallet made there is there after a reload', async ({ page }) => {
    await page.goto(ADDRESS);
    await expect(page).toHaveURL(/^http:\/\/127\.0\.0\.1:8791\/app\/index\.html/);
    await expect(page.getByRole('heading', { name: 'Set up your wallet' })).toBeVisible({ timeout: 240_000 });
    await page.getByLabel('Passcode', { exact: true }).fill('desktop-pass-1');
    await page.getByLabel('Confirm passcode', { exact: true }).fill('desktop-pass-1');
    await page.getByRole('button', { name: 'Create wallet' }).click();
    await expect(page.getByRole('heading', { name: 'Roles', exact: true, level: 1 })).toBeVisible({ timeout: 180_000 });
    await page.reload();
    await expect(page.getByRole('heading', { name: 'Open My Wallet' })).toBeVisible({ timeout: 240_000 });
});

test('localhost is sent on to the one address, so there is one wallet', async ({ page }) => {
    await page.goto('http://localhost:8791/');
    await expect(page).toHaveURL(/^http:\/\/127\.0\.0\.1:8791\/app\/index\.html/);
});
