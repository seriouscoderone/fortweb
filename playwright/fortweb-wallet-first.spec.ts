import { expect, test, type Page } from '@playwright/test';

// Wallet-first launch against the real Pyodide runtime: a fresh install shows
// "Set up your wallet"; afterwards every launch goes straight to the default
// vault's unlock screen instead of the vault picker.

async function waitForSetup(page: Page): Promise<void> {
    await expect(page.getByRole('heading', { name: 'Set up your wallet' })).toBeVisible({ timeout: 240_000 });
}

test('fresh install sets up the wallet, then launches straight into it', async ({ page }) => {
    test.setTimeout(420_000);
    await page.goto('/fortweb/app/index.html');
    await waitForSetup(page);
    await expect(page.locator('.home-splash')).toHaveCount(0);

    const passcode = page.getByLabel('Passcode', { exact: true });
    const confirm = page.getByLabel('Confirm passcode', { exact: true });
    const create = page.getByRole('button', { name: 'Create wallet' });
    const status = page.locator('[data-setup-status]');

    await create.click();
    await expect(status).toHaveText('Choose a passcode of at least 8 characters.');

    await passcode.fill('short');
    await confirm.fill('short');
    await create.click();
    await expect(status).toHaveText('Choose a passcode of at least 8 characters.');

    await passcode.fill('correct-horse-9');
    await confirm.fill('correct-horse-8');
    await create.click();
    await expect(status).toHaveText("The passcodes don't match.");

    await confirm.fill('correct-horse-9');
    await create.click();
    await expect(page.getByRole('heading', { name: 'Local Identifiers', exact: true })).toBeVisible({ timeout: 180_000 });
    await expect(page).toHaveURL(/#\/vaults\/my-wallet\/identifiers$/);

    // Next launch: straight to the default vault's unlock, no picker, no setup.
    await page.reload();
    await expect(page).toHaveURL(/#\/vaults\/my-wallet\/unlock$/, { timeout: 240_000 });
    await expect(page.getByRole('heading', { name: 'Open My Wallet' })).toBeVisible();

    await page.getByLabel('Passcode').fill('correct-horse-9');
    await page.getByRole('button', { name: 'Open', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Local Identifiers', exact: true })).toBeVisible({ timeout: 180_000 });
});

test('Make default in Settings changes which vault opens at launch', async ({ page }) => {
    test.setTimeout(480_000);
    await page.goto('/fortweb/app/index.html');
    await waitForSetup(page);
    await page.getByLabel('Passcode', { exact: true }).fill('first-pass-1');
    await page.getByLabel('Confirm passcode', { exact: true }).fill('first-pass-1');
    await page.getByRole('button', { name: 'Create wallet' }).click();
    await expect(page.getByRole('heading', { name: 'Local Identifiers', exact: true })).toBeVisible({ timeout: 180_000 });

    // Advanced path: lock, then add a second vault from the drawer.
    await page.getByRole('button', { name: 'Lock vault' }).click();
    await expect(page.getByRole('heading', { name: 'Open My Wallet' })).toBeVisible();
    await page.getByRole('button', { name: 'Vaults', exact: true }).click();
    const drawer = page.getByRole('dialog', { name: 'Vault switcher' });
    await drawer.getByRole('button', { name: 'Initialize New Vault' }).click();
    await page.locator('[data-create-vault-form] input[name="name"]').fill('Side');
    await page.locator('[data-create-vault-form] input[name="passcode"]').fill('side-pass-1');
    await page.locator('[data-dialog-submit]').click();
    await expect(page.getByRole('heading', { name: 'Open Side' })).toBeVisible({ timeout: 180_000 });

    await page.getByRole('button', { name: 'Vaults', exact: true }).click();
    await expect(drawer.locator('[data-vault-id="my-wallet"] .lk-drawer__item-default')).toHaveText('Default');
    await expect(drawer.locator('[data-vault-id="side"] .lk-drawer__item-default')).toHaveCount(0);
    await page.keyboard.press('Escape');

    await page.getByLabel('Passcode').fill('side-pass-1');
    await page.getByRole('button', { name: 'Open', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Local Identifiers', exact: true })).toBeVisible({ timeout: 180_000 });
    await page.evaluate(() => { window.location.hash = '#/vaults/side/settings'; });
    await expect(page.getByRole('heading', { name: 'Startup' })).toBeVisible({ timeout: 60_000 });
    await page.getByRole('button', { name: 'Make default' }).click();
    await expect(page.getByText('Side opens when the app starts.')).toBeVisible();

    await page.reload();
    await expect(page).toHaveURL(/#\/vaults\/side\/unlock$/, { timeout: 240_000 });
});

test('a vault closed by a background runtime reset returns to its unlock screen', async ({ page }) => {
    test.setTimeout(420_000);
    await page.goto('/fortweb/app/index.html');
    await waitForSetup(page);
    await page.getByLabel('Passcode', { exact: true }).fill('reset-pass-1');
    await page.getByLabel('Confirm passcode', { exact: true }).fill('reset-pass-1');
    await page.getByRole('button', { name: 'Create wallet' }).click();
    await expect(page.getByRole('heading', { name: 'Local Identifiers', exact: true })).toBeVisible({ timeout: 180_000 });

    // Leave a dialog open on a vault page, as in the field report.
    await page.evaluate(() => { window.location.hash = '#/vaults/my-wallet/remotes'; });
    await page.getByRole('button', { name: 'Add Remote Identifier', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Add Remote Identifier', exact: true });
    await expect(dialog).toBeVisible();

    // Simulate the app being in the background for 31 s, then returning.
    await page.evaluate(() => {
        let hidden = false;
        Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
        hidden = true;
        document.dispatchEvent(new Event('visibilitychange'));
        const realNow = Date.now.bind(Date);
        Date.now = () => realNow() + 31_000;
        hidden = false;
        document.dispatchEvent(new Event('visibilitychange'));
    });

    await expect(page).toHaveURL(/#\/vaults\/my-wallet\/unlock$/);
    await expect(page.getByRole('heading', { name: 'Open My Wallet' })).toBeVisible();
    await expect(dialog).toHaveCount(0);
    await expect(page.locator('#app-root')).toHaveJSProperty('inert', false);

    // The fresh runtime opens the vault again and returns to where the user was.
    await page.getByLabel('Passcode').fill('reset-pass-1');
    await page.getByRole('button', { name: 'Open', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Remote Identifiers', exact: true })).toBeVisible({ timeout: 180_000 });
    await expect(page).toHaveURL(/#\/vaults\/my-wallet\/remotes$/);
});
