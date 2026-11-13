import { expect, test } from '@playwright/test';
import { FIXTURE_HOST, routeFixtureSource } from './fixtures/mad-source.js';
import {
    setUpWallet, heldSource, rolesPage, openAddress, showDropinRoles, unlock, closePhoneMenu, myRoles,
    takeOnWithNewIdentifier, expectFrom,
} from './fixtures/roles.js';

// The last read kept by the app.

test.describe('The last read', () => {
    test.setTimeout(480_000);

    test('survives opening and closing the phone menu', async ({ context, page }) => {
        await page.setViewportSize({ width: 412, height: 915 });
        await routeFixtureSource(context);
        await setUpWallet(page);
        await showDropinRoles(page);
        const roles = rolesPage(page);
        await page.getByRole('button', { name: 'Open navigation' }).click();
        await closePhoneMenu(page);
        await expect(roles.address).toHaveValue(FIXTURE_HOST);
        await expect(roles.cards).toHaveCount(3);
        await expect(roles.card('dropin-class').getByRole('button', { name: 'Hide roles' })).toBeVisible();
    });

    test('a read still running when the phone menu opens is shown when it finishes', async ({ context, page }) => {
        await page.setViewportSize({ width: 412, height: 915 });
        const held = heldSource();
        await routeFixtureSource(context, { holdSource: held.option });
        await setUpWallet(page);
        const roles = rolesPage(page);
        await openAddress(page, FIXTURE_HOST);
        try {
            await held.arrived;
            await page.getByRole('button', { name: 'Open navigation' }).click();
            await closePhoneMenu(page);
            await expect(roles.address).toHaveValue(FIXTURE_HOST);
            await expect(roles.status).toHaveText('Reading...');
            await expect(roles.open).toBeDisabled();
        } finally {
            held.release();
        }
        await expect(roles.cards).toHaveCount(3, { timeout: 90_000 });
        await expect(roles.open).toBeEnabled();
        await expect(roles.announcer).toHaveText('mad.example.test: 3 publications, 26 definitions checked');
        expect(held.count()).toBe(1);
    });

    test('is cleared when the vault is locked', async ({ context, page }) => {
        await routeFixtureSource(context);
        await setUpWallet(page);
        await showDropinRoles(page);
        await page.getByRole('button', { name: 'Lock vault' }).click();
        await unlock(page);
        const roles = rolesPage(page);
        await expect(roles.address).toHaveValue('');
        await expect(roles.cards).toHaveCount(0);
        await expect(roles.host).toHaveCount(0);
    });

    test('is kept, and takes a role on, after a new vault was refused for its name', async ({ context, page }) => {
        await routeFixtureSource(context);
        await setUpWallet(page);
        await showDropinRoles(page);
        // To the vault picker, where a new vault named like the open one is refused, and back to the open vault.
        await page.locator('a.topbar__brand-link').click();
        await expect(page.locator('.home-splash')).toBeVisible({ timeout: 60_000 });
        const drawer = page.getByRole('dialog', { name: 'Vault switcher' });
        await page.getByRole('button', { name: 'Vaults', exact: true }).click();
        await drawer.getByRole('button', { name: 'Initialize New Vault' }).click();
        const form = page.locator('[data-create-vault-form]');
        await form.locator('input[name="name"]').fill('My Wallet');
        await page.locator('[data-dialog-submit]').click();
        await expect(page.locator('[data-create-vault-status]'))
            .toHaveText('Vault \'My Wallet\' already exists.', { timeout: 150_000 });
        await page.locator('[data-dialog-cancel]').click();
        await expect(form).toHaveCount(0);
        await page.getByRole('button', { name: 'Vaults', exact: true }).click();
        await drawer.locator('[data-vault-id="my-wallet"]').click();
        await expect(page.getByRole('heading', { name: 'Roles', exact: true, level: 1 }))
            .toBeVisible({ timeout: 60_000 });
        // The page still shows the read, and the engine still has it: the role is taken on.
        const roles = rolesPage(page);
        await expect(roles.address).toHaveValue(FIXTURE_HOST);
        await expect(roles.cards).toHaveCount(3);
        await expect(roles.card('dropin-class').getByRole('button', { name: 'Hide roles' })).toBeVisible();
        await takeOnWithNewIdentifier(page, 'cashier');
        await expectFrom(myRoles(page).item('cashier'), 'dropin-class');
    });

    test('a read that finishes after leaving the page announces nothing and is there on coming back',
        async ({ context, page }) => {
            const held = heldSource();
            const requests = await routeFixtureSource(context, { holdSource: held.option });
            await setUpWallet(page);
            const roles = rolesPage(page);
            await openAddress(page, FIXTURE_HOST);
            const nav = page.getByRole('navigation', { name: 'Vault navigation' });
            try {
                await held.arrived;
                await nav.getByRole('link', { name: 'Identifiers', exact: true }).click();
                await expect(page.getByRole('heading', { name: 'Local Identifiers', exact: true })).toBeVisible();
                await page.evaluate(() => {
                    const announced: string[] = [];
                    (window as unknown as { announced: string[] }).announced = announced;
                    const region = document.querySelector('#fw-live-region');
                    if (region) {
                        new MutationObserver(() => announced.push(region.textContent ?? ''))
                            .observe(region, { childList: true, characterData: true, subtree: true });
                    }
                });
            } finally {
                held.release();
            }
            // The read finishes while the Identifiers page is shown: every definition is fetched, then checked.
            await expect.poll(() => requests.definitions.length, { timeout: 90_000 }).toBe(26);
            await page.waitForTimeout(2_000);
            await expect(page.getByRole('heading', { name: 'Local Identifiers', exact: true })).toBeVisible();
            expect(await page.evaluate(() => (window as unknown as { announced: string[] }).announced
                .filter((text) => text !== ''))).toEqual([]);
            await nav.getByRole('link', { name: 'Roles', exact: true }).click();
            await expect(roles.address).toHaveValue(FIXTURE_HOST);
            await expect(roles.cards).toHaveCount(3, { timeout: 90_000 });
            await expect(roles.status).toHaveText('');
            // Had the read still been running on coming back, this page would now announce its result (one frame
            // after showing it): a wait above that is too short fails here instead of passing.
            await page.evaluate(() => new Promise((resolve) => {
                requestAnimationFrame(() => requestAnimationFrame(resolve));
            }));
            expect(await page.evaluate(() => (window as unknown as { announced: string[] }).announced
                .filter((text) => text !== ''))).toEqual([]);
        });
});
