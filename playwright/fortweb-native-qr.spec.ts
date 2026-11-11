import { expect, test, type Page } from '@playwright/test';

// Fake of the Fortoid Android host bridge (window.bridge): answers
// native_request messages with native_response messages, like
// NativeBridgeProtocol.kt. Each test sets window.__qrResponse.
async function installFakeAndroidBridge(page: Page): Promise<void> {
    await page.addInitScript(() => {
        const listeners: Array<(event: { data: string }) => void> = [];
        const w = window as unknown as Record<string, unknown>;
        w['__nativeRequests'] = [];
        w['bridge'] = {
            addEventListener(type: string, listener: (event: { data: string }) => void) {
                if (type === 'message') listeners.push(listener);
            },
            postMessage(raw: string) {
                const request = JSON.parse(raw);
                if (request.type !== 'native_request') return;
                (w['__nativeRequests'] as unknown[]).push(request);
                const outcome = w['__qrResponse'] as { ok: boolean; result?: unknown; error?: unknown };
                const response = { type: 'native_response', id: request.id, ...outcome };
                setTimeout(() => listeners.forEach((listener) => listener({ data: JSON.stringify(response) })), 0);
            },
        };
    });
}

async function openAddRemoteDialog(page: Page) {
    await page.goto('/fortweb/app/index.html#/_fixtures/remotes/empty');
    await page.getByRole('button', { name: 'Add Remote Identifier', exact: true }).click();
    return page.getByRole('dialog', { name: 'Add Remote Identifier', exact: true });
}

const OOBI = 'https://witness.example.com/oobi/EKYLUMmNPZeEs77Zvclf0bSN5IN-mLfLpx2ySb-HDlk4/witness';

test('Scan QR fills the OOBI URL from a scanned OOBI', async ({ page }) => {
    await installFakeAndroidBridge(page);
    await page.addInitScript((oobi) => {
        (window as unknown as Record<string, unknown>)['__qrResponse'] = {
            ok: true,
            result: { text: oobi, format: 'QR_CODE', isOobi: true, oobiAid: 'EKYLUMmNPZeEs77Zvclf0bSN5IN-mLfLpx2ySb-HDlk4', truncated: false },
        };
    }, OOBI);

    const dialog = await openAddRemoteDialog(page);
    await dialog.getByRole('button', { name: 'Scan QR code', exact: true }).click();

    await expect(dialog.getByRole('textbox', { name: 'OOBI URL' })).toHaveValue(OOBI);
    await expect(dialog.locator('[data-resolve-remote-status]')).toHaveText('');
    const requests = await page.evaluate(() => (window as unknown as Record<string, unknown>)['__nativeRequests']);
    expect(requests).toEqual([expect.objectContaining({ type: 'native_request', capability: 'qr.scan' })]);
});

test('Scan QR fills non-OOBI text but warns', async ({ page }) => {
    await installFakeAndroidBridge(page);
    await page.addInitScript(() => {
        (window as unknown as Record<string, unknown>)['__qrResponse'] = {
            ok: true,
            result: { text: 'hello world', format: 'QR_CODE', isOobi: false, truncated: false },
        };
    });

    const dialog = await openAddRemoteDialog(page);
    await dialog.getByRole('button', { name: 'Scan QR code', exact: true }).click();

    await expect(dialog.getByRole('textbox', { name: 'OOBI URL' })).toHaveValue('hello world');
    await expect(dialog.locator('[data-resolve-remote-status]')).toHaveText("This doesn't look like an OOBI link.");
});

test('Cancelled scan leaves the field alone without an error', async ({ page }) => {
    await installFakeAndroidBridge(page);
    await page.addInitScript(() => {
        (window as unknown as Record<string, unknown>)['__qrResponse'] = {
            ok: false,
            error: { code: 'cancelled', message: 'scan cancelled' },
        };
    });

    const dialog = await openAddRemoteDialog(page);
    const field = dialog.getByRole('textbox', { name: 'OOBI URL' });
    await field.fill('https://typed.example/oobi/x');
    await dialog.getByRole('button', { name: 'Scan QR code', exact: true }).click();

    await expect(dialog.getByRole('button', { name: 'Scan QR code', exact: true })).toBeEnabled();
    await expect(field).toHaveValue('https://typed.example/oobi/x');
    await expect(dialog.locator('[data-resolve-remote-status]')).toHaveText('');
});

test('Scanner failure is shown in the dialog', async ({ page }) => {
    await installFakeAndroidBridge(page);
    await page.addInitScript(() => {
        (window as unknown as Record<string, unknown>)['__qrResponse'] = {
            ok: false,
            error: { code: 'failed', message: 'camera unavailable' },
        };
    });

    const dialog = await openAddRemoteDialog(page);
    await dialog.getByRole('button', { name: 'Scan QR code', exact: true }).click();

    await expect(dialog.locator('[data-resolve-remote-status]')).toHaveText('QR scan failed: camera unavailable');
});

test('No Scan QR button outside the Android app', async ({ page }) => {
    const dialog = await openAddRemoteDialog(page);

    await expect(dialog.getByRole('textbox', { name: 'OOBI URL' })).toBeVisible();
    await expect(dialog.getByRole('button', { name: 'Scan QR code' })).toHaveCount(0);
});
