import { expect, test } from '@playwright/test';
import { FIXTURE_HOST, routeFixtureSource } from './fixtures/mad-source.js';
import {
    setUpWallet, heldSource, rolesPage, openAddress, showDropinRoles, unlock, closePhoneMenu,
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
        });
});
