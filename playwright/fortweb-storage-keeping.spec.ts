import { expect, test, type Page } from '@playwright/test';

import { setUpWallet, unlock } from './fixtures/roles';

interface Asked { asked: number; kept: boolean }

/**
 * The browser's answers are made the test's own: it counts how often it is asked and gives `answers` in turn, the
 * last one again from then on. An answer of 'rejects' makes the ask fail instead.
 */
async function browserThatSays(page: Page, ...answers: Array<boolean | 'rejects'>): Promise<void> {
    await page.addInitScript((given) => {
        const state = { asked: 0, kept: false };
        (window as unknown as { storageAsked: typeof state }).storageAsked = state;
        StorageManager.prototype.persist = async function persist(): Promise<boolean> {
            const answer = given[Math.min(state.asked, given.length - 1)];
            state.asked += 1;
            if (answer === 'rejects') {
                throw new TypeError('The browser would not answer.');
            }
            state.kept = answer === true;
            return state.kept;
        };
        StorageManager.prototype.persisted = async function persisted(): Promise<boolean> {
            return state.kept;
        };
    }, answers);
}

/** The browser fails every read of whether it keeps the storage, whatever it was asked before. */
async function browserThatWillNotSay(page: Page): Promise<void> {
    await page.addInitScript(() => {
        StorageManager.prototype.persisted = async function persisted(): Promise<boolean> {
            throw new TypeError('The browser would not say.');
        };
    });
}

/** The browser never settles a read of whether it keeps the storage. */
async function browserThatNeverAnswers(page: Page): Promise<void> {
    await page.addInitScript(() => {
        StorageManager.prototype.persisted = function persisted(): Promise<boolean> {
            return new Promise<boolean>(() => {});
        };
    });
}

/** From the moment `throwOnLookUp(page)` is called, merely looking up `navigator.storage` throws. */
async function browserWhoseStorageLookUpCanThrow(page: Page): Promise<void> {
    await page.addInitScript(() => {
        const real = Object.getOwnPropertyDescriptor(Navigator.prototype, 'storage');
        Object.defineProperty(Navigator.prototype, 'storage', {
            configurable: true,
            get(this: Navigator) {
                if ((window as unknown as { storageLookUpThrows?: boolean }).storageLookUpThrows) {
                    throw new TypeError('The browser would not give its storage manager.');
                }
                return real?.get?.call(this);
            },
        });
    });
}

async function throwOnLookUp(page: Page): Promise<void> {
    await page.evaluate(() => {
        (window as unknown as { storageLookUpThrows?: boolean }).storageLookUpThrows = true;
    });
}

/** Stands in for the phone app: its bridge is there, and offers nothing. */
async function insideThePhoneApp(page: Page): Promise<void> {
    await page.addInitScript(() => {
        const listeners: Array<(event: { data: string }) => void> = [];
        (window as unknown as Record<string, unknown>)['bridge'] = {
            addEventListener(type: string, listener: (event: { data: string }) => void) {
                if (type === 'message') listeners.push(listener);
            },
            postMessage(raw: string) {
                const request = JSON.parse(raw) as { type?: string; id?: string };
                if (request.type !== 'native_request') return;
                const response = {
                    type: 'native_response', id: request.id, ok: false, error: { code: 'unknown_capability', message: 'none' },
                };
                setTimeout(() => listeners.forEach((listener) => listener({ data: JSON.stringify(response) })), 0);
            },
        };
    });
}

async function asked(page: Page): Promise<Asked> {
    return page.evaluate(() => (window as unknown as { storageAsked: Asked }).storageAsked);
}

async function openSettings(page: Page): Promise<void> {
    await page.getByRole('navigation', { name: 'Vault navigation' }).getByRole('link', { name: 'Settings', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Settings', exact: true, level: 1 })).toBeVisible({ timeout: 60_000 });
}

function keptByTheBrowser(page: Page) {
    return page.locator('.detail-item', { has: page.getByText('Kept by the Browser', { exact: true }) }).locator('dd');
}

test.describe('Keeping the wallet\'s storage', () => {
    test('making a wallet asks the browser once, and Settings says the browser agreed', async ({ page }) => {
        await browserThatSays(page, true);
        await setUpWallet(page);
        await expect.poll(async () => (await asked(page)).asked).toBe(1);
        await openSettings(page);
        await expect(keptByTheBrowser(page)).toHaveText('Yes');
        expect((await asked(page)).asked).toBe(1);
    });

    test('Settings says so when the browser did not agree', async ({ page }) => {
        await browserThatSays(page, false);
        await setUpWallet(page);
        await expect.poll(async () => (await asked(page)).asked).toBe(1);
        await openSettings(page);
        await expect(keptByTheBrowser(page))
            .toHaveText('No. The browser may remove this wallet\'s storage when it runs short of space.');
        expect((await asked(page)).asked).toBe(1);
    });

    test('a browser that said no is asked again when the vault is next opened, not by a refused passcode, and Settings follows its answer', async ({ page }) => {
        await browserThatSays(page, false, true);
        await setUpWallet(page);
        await expect.poll(async () => (await asked(page)).asked).toBe(1);
        await page.getByRole('button', { name: 'Lock vault' }).click();
        await expect(page.getByRole('heading', { name: 'Open My Wallet' })).toBeVisible({ timeout: 60_000 });
        await page.getByLabel('Passcode').fill('not-the-passcode');
        await page.getByRole('button', { name: 'Open', exact: true }).click();
        await expect(page.locator('#unlock-status')).toHaveClass(/status-line--error/, { timeout: 60_000 });
        expect((await asked(page)).asked).toBe(1);
        await unlock(page);
        await expect.poll(async () => (await asked(page)).asked).toBe(2);
        await openSettings(page);
        await expect(keptByTheBrowser(page)).toHaveText('Yes');
        expect((await asked(page)).asked).toBe(2);
    });

    test('Settings says the browser does not say when its read fails', async ({ page }) => {
        await browserThatSays(page, true);
        await browserThatWillNotSay(page);
        await setUpWallet(page);
        await expect.poll(async () => (await asked(page)).asked).toBe(1);
        await openSettings(page);
        await expect(keptByTheBrowser(page)).toHaveText('This browser does not say.');
        expect((await asked(page)).asked).toBe(1);
    });

    test('Settings is drawn, saying the browser does not say, when the browser never answers the read', async ({ page }) => {
        await browserThatSays(page, true);
        await browserThatNeverAnswers(page);
        await setUpWallet(page);
        await openSettings(page);
        await expect(keptByTheBrowser(page)).toHaveText('This browser does not say.');
        expect((await asked(page)).asked).toBe(1);
    });

    test('Settings says the browser does not say when looking up its storage throws', async ({ page }) => {
        await browserThatSays(page, true);
        await browserWhoseStorageLookUpCanThrow(page);
        await setUpWallet(page);
        await expect.poll(async () => (await asked(page)).asked).toBe(1);
        await throwOnLookUp(page);
        await openSettings(page);
        await expect(keptByTheBrowser(page)).toHaveText('This browser does not say.');
        expect((await asked(page)).asked).toBe(1);
    });

    test('a failed ask leaves the vault to open, with no error on the page', async ({ page }) => {
        const pageErrors: string[] = [];
        page.on('pageerror', (error) => pageErrors.push(error.message));
        await browserThatSays(page, 'rejects');
        await setUpWallet(page);
        await expect.poll(async () => (await asked(page)).asked).toBe(1);
        await expect(page.getByRole('heading', { name: 'Roles', exact: true, level: 1 })).toBeVisible();
        await expect(page.locator('.ui-toast--error, .status-line--error, [role="alert"]')).toHaveCount(0);
        expect(pageErrors).toEqual([]);
    });

    test('inside the phone app the wallet still asks, and Settings has no line about a browser', async ({ page }) => {
        await browserThatSays(page, false);
        await insideThePhoneApp(page);
        await setUpWallet(page);
        await expect.poll(async () => (await asked(page)).asked).toBe(1);
        await openSettings(page);
        await expect(page.getByText('Storage Backend', { exact: true })).toBeVisible();
        await expect(page.getByText('Kept by the Browser', { exact: true })).toHaveCount(0);
        expect((await asked(page)).asked).toBe(1);
    });
});
