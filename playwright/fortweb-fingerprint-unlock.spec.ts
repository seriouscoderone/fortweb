import { expect, test, type Page } from '@playwright/test';

// Fake of the Fortoid Android bridge with an in-memory secret store.
// window.__fp controls behaviour; window.__nativeRequests records calls.
type FakeOptions = {
    biometricAvailable?: boolean;
    stored?: Record<string, string>;
    retrieveError?: { code: string; message: string };
    storeError?: { code: string; message: string };
};

async function installFakeBridge(page: Page, options: FakeOptions = {}): Promise<void> {
    await page.addInitScript((opts: FakeOptions) => {
        const w = window as unknown as Record<string, unknown>;
        const store: Record<string, string> = { ...(opts.stored ?? {}) };
        const listeners: Array<(event: { data: string }) => void> = [];
        const requests: Array<Record<string, unknown>> = [];
        w['__nativeRequests'] = requests;
        w['__secretStore'] = store;

        function answer(request: { capability: string; params: Record<string, string> }) {
            const key = request.params.key;
            switch (request.capability) {
                case 'secret.status':
                    return { ok: true, result: { stored: key in store, biometricAvailable: opts.biometricAvailable !== false } };
                case 'secret.store':
                    if (opts.storeError) return { ok: false, error: opts.storeError };
                    store[key] = request.params.value;
                    return { ok: true, result: { stored: true } };
                case 'secret.retrieve':
                    if (opts.retrieveError) return { ok: false, error: opts.retrieveError };
                    if (!(key in store)) return { ok: false, error: { code: 'bad_request', message: 'nothing stored' } };
                    return { ok: true, result: { value: store[key] } };
                case 'secret.delete': {
                    const existed = key in store;
                    delete store[key];
                    return { ok: true, result: { deleted: existed } };
                }
                default:
                    return { ok: false, error: { code: 'unknown_capability', message: request.capability } };
            }
        }

        w['bridge'] = {
            addEventListener(type: string, listener: (event: { data: string }) => void) {
                if (type === 'message') listeners.push(listener);
            },
            postMessage(raw: string) {
                const request = JSON.parse(raw);
                if (request.type !== 'native_request') return;
                requests.push(request);
                const response = { type: 'native_response', id: request.id, ...answer(request) };
                setTimeout(() => listeners.forEach((listener) => listener({ data: JSON.stringify(response) })), 0);
            },
        };
    }, options);
}

const requests = (page: Page) =>
    page.evaluate(() => (window as unknown as Record<string, Array<Record<string, unknown>>>)['__nativeRequests']);
const capabilities = async (page: Page) => (await requests(page)).map((r) => r['capability']);
const secretStore = (page: Page) =>
    page.evaluate(() => (window as unknown as Record<string, Record<string, string>>)['__secretStore']);

async function storedKeyFor(page: Page): Promise<string> {
    // Same derivation as app/runtime/vault-fingerprint.ts, computed in the page.
    return page.evaluate(async () => {
        const bytes = new TextEncoder().encode('fixture-vault-001|2026-04-01T12:00:00Z');
        const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
        return 'vault-' + Array.from(digest.slice(0, 16), (b) => b.toString(16).padStart(2, '0')).join('');
    });
}

async function openWithPasscode(page: Page, passcode = 'demo-passcode'): Promise<void> {
    await page.locator('#unlock-passcode').fill(passcode);
    await page.getByRole('button', { name: 'Open', exact: true }).click();
}

const offerDialog = (page: Page) => page.getByRole('dialog').filter({ hasText: 'Use fingerprint' });

test('no fingerprint UI outside the Android app', async ({ page }) => {
    await page.goto('/fortweb/app/index.html#/_fixtures/unlock');
    await openWithPasscode(page);

    await expect(page.getByRole('button', { name: 'Unlock with fingerprint' })).toBeHidden();
    await expect(offerDialog(page)).toHaveCount(0);
});

test('after a passcode unlock the wallet offers fingerprint and stores the passcode', async ({ page }) => {
    await installFakeBridge(page);
    await page.goto('/fortweb/app/index.html#/_fixtures/unlock');
    await expect(page.getByRole('button', { name: 'Unlock with fingerprint' })).toBeHidden();

    await openWithPasscode(page, 'my secret passcode');
    await expect(offerDialog(page)).toBeVisible();
    await offerDialog(page).getByRole('button', { name: 'Use fingerprint', exact: true }).click();

    await expect(offerDialog(page)).toHaveCount(0);
    expect(await secretStore(page)).toEqual({ [await storedKeyFor(page)]: 'my secret passcode' });
});

test('Not now is remembered for the vault', async ({ page }) => {
    await installFakeBridge(page);
    await page.goto('/fortweb/app/index.html#/_fixtures/unlock');
    await openWithPasscode(page);
    await offerDialog(page).getByRole('button', { name: 'Not now', exact: true }).click();
    await expect(offerDialog(page)).toHaveCount(0);

    await page.reload();
    await openWithPasscode(page);
    await expect(page.locator('[data-unlock-status]')).toHaveText(/Opening vault/);
    await page.waitForTimeout(300);
    await expect(offerDialog(page)).toHaveCount(0);
    expect(await capabilities(page)).not.toContain('secret.store');
});

test('no offer when the phone has no usable biometrics', async ({ page }) => {
    await installFakeBridge(page, { biometricAvailable: false });
    await page.goto('/fortweb/app/index.html#/_fixtures/unlock');
    await openWithPasscode(page);
    await page.waitForTimeout(300);

    await expect(offerDialog(page)).toHaveCount(0);
});

test('a vault with a saved passcode prompts for the fingerprint immediately and opens', async ({ page }) => {
    await installFakeBridge(page);
    await page.addInitScript(async () => {
        const bytes = new TextEncoder().encode('fixture-vault-001|2026-04-01T12:00:00Z');
        const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
        const key = 'vault-' + Array.from(digest.slice(0, 16), (b) => b.toString(16).padStart(2, '0')).join('');
        (window as unknown as Record<string, Record<string, string>>)['__secretStore'][key] = 'saved-passcode';
    });
    await page.goto('/fortweb/app/index.html#/_fixtures/unlock');

    await expect(page.getByRole('button', { name: 'Unlock with fingerprint' })).toBeVisible();
    await expect.poll(() => capabilities(page)).toEqual(['secret.status', 'secret.retrieve']);
    await expect(page.locator('[data-unlock-status]')).toHaveText(/Opening vault/);
    await expect(offerDialog(page)).toHaveCount(0);
});

test('cancelling the fingerprint prompt falls back to the passcode quietly', async ({ page }) => {
    await installFakeBridge(page, { retrieveError: { code: 'cancelled', message: 'User canceled' } });
    await page.addInitScript(async () => {
        const bytes = new TextEncoder().encode('fixture-vault-001|2026-04-01T12:00:00Z');
        const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
        const key = 'vault-' + Array.from(digest.slice(0, 16), (b) => b.toString(16).padStart(2, '0')).join('');
        (window as unknown as Record<string, Record<string, string>>)['__secretStore'][key] = 'saved-passcode';
    });
    await page.goto('/fortweb/app/index.html#/_fixtures/unlock');

    await expect.poll(() => capabilities(page)).toContain('secret.retrieve');
    await expect(page.locator('[data-unlock-status]')).toHaveText('');
    await expect(page.getByRole('button', { name: 'Unlock with fingerprint' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Open', exact: true })).toBeEnabled();
});

test('a new enrollment on the phone explains why fingerprint unlock stopped', async ({ page }) => {
    await installFakeBridge(page, { retrieveError: { code: 'invalidated', message: 'wiped' } });
    await page.addInitScript(async () => {
        const bytes = new TextEncoder().encode('fixture-vault-001|2026-04-01T12:00:00Z');
        const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
        const key = 'vault-' + Array.from(digest.slice(0, 16), (b) => b.toString(16).padStart(2, '0')).join('');
        (window as unknown as Record<string, Record<string, string>>)['__secretStore'][key] = 'saved-passcode';
    });
    await page.goto('/fortweb/app/index.html#/_fixtures/unlock');

    await expect(page.locator('[data-unlock-status]')).toContainText('new fingerprint or face');
    await expect(page.getByRole('button', { name: 'Unlock with fingerprint' })).toBeHidden();
});

test('Forget fingerprint removes the saved passcode', async ({ page }) => {
    await installFakeBridge(page, { retrieveError: { code: 'cancelled', message: 'User canceled' } });
    await page.addInitScript(async () => {
        const bytes = new TextEncoder().encode('fixture-vault-001|2026-04-01T12:00:00Z');
        const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
        const key = 'vault-' + Array.from(digest.slice(0, 16), (b) => b.toString(16).padStart(2, '0')).join('');
        (window as unknown as Record<string, Record<string, string>>)['__secretStore'][key] = 'saved-passcode';
    });
    await page.goto('/fortweb/app/index.html#/_fixtures/unlock');
    await expect.poll(() => capabilities(page)).toContain('secret.retrieve');

    await page.getByRole('button', { name: 'Forget fingerprint' }).click();

    await expect(page.getByRole('button', { name: 'Unlock with fingerprint' })).toBeHidden();
    await expect(page.getByRole('button', { name: 'Forget fingerprint' })).toBeHidden();
    expect(await secretStore(page)).toEqual({});
});

test('a saved passcode that no longer works is removed', async ({ page }) => {
    await installFakeBridge(page);
    await page.addInitScript(async () => {
        const bytes = new TextEncoder().encode('fixture-vault-001|2026-04-01T12:00:00Z');
        const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
        const key = 'vault-' + Array.from(digest.slice(0, 16), (b) => b.toString(16).padStart(2, '0')).join('');
        (window as unknown as Record<string, Record<string, string>>)['__secretStore'][key] = 'old-passcode';
    });
    await page.goto('/fortweb/app/index.html#/_fixtures/unlock/auth-fails');

    await expect(page.locator('[data-unlock-status]')).toContainText('fingerprint unlock was turned off');
    expect(await secretStore(page)).toEqual({});
    await expect(page.getByRole('button', { name: 'Unlock with fingerprint' })).toBeHidden();
});
