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
