import { expect, test } from '@playwright/test';
import { FIXTURE_HOST, routeFixtureSource } from './fixtures/mad-source.js';
import {
    DROPIN_CLASS, setUpWallet, startRequest, finishRequest, rolesPage, NOT_APPOINTED_SENTENCE, OPEN_AGAIN,
    editSource, openAddress, takeOnDialog, ALREADY_TAKEN, CASHIER_DUTIES, showDropinRoles, myRoles, startTakingOn,
    takeOnWithNewIdentifier, unlock, sendToBackgroundFor31Seconds,
} from './fixtures/roles.js';

// Taking on a role and My roles.

test.describe('Taking on a role', () => {
    test.setTimeout(480_000);

    test('taking on cashier with a new identifier lists it under My roles', async ({ context, page }) => {
        await routeFixtureSource(context);
        await setUpWallet(page);
        await showDropinRoles(page);
        const dialog = await startTakingOn(page, 'cashier');
        // A vault with no identifiers offers only a new one, named after the role.
        await expect(dialog.createNew).toBeChecked();
        await expect(dialog.selectExisting).toHaveCount(0);
        await expect(dialog.name).toHaveValue('cashier');
        await dialog.takeOn.click();
        await expect(dialog.dialog).toHaveCount(0, { timeout: 90_000 });

        const mine = myRoles(page);
        await expect(mine.items).toHaveCount(1);
        const cashier = mine.item('cashier');
        await expect(cashier.getByText('dropin-class at mad.example.test', { exact: true })).toBeVisible();
        await expect(cashier.getByText('Identifier: cashier', { exact: true })).toBeVisible();
        await expect(cashier.getByText('Not appointed yet', { exact: true })).toBeVisible();
        await expect(cashier.getByText(NOT_APPOINTED_SENTENCE, { exact: true })).toBeVisible();
        await expect(cashier.locator('[data-responsibility]')).toHaveText(CASHIER_DUTIES);
        await expect(mine.region.getByText('No roles yet. Open an address to see what you can take on.'))
            .toHaveCount(0);
        // Focus lands on the role just taken on.
        await expect(cashier.getByRole('heading', { level: 3, name: 'cashier' })).toBeFocused();
        // The read is still there, as it was left.
        const roles = rolesPage(page);
        await expect(roles.cards).toHaveCount(3);
        await expect(roles.card('dropin-class').getByRole('button', { name: 'Hide roles' })).toBeVisible();

        const nav = page.getByRole('navigation', { name: 'Vault navigation' });
        await nav.getByRole('link', { name: 'Identifiers', exact: true }).click();
        await expect(page.getByRole('link', { name: 'cashier', exact: true })).toBeVisible();
    });

    test('no request leaves the page while taking on a role', async ({ context, page }) => {
        await routeFixtureSource(context);
        await setUpWallet(page);
        await showDropinRoles(page);
        const dialog = await startTakingOn(page, 'cashier');
        const appOrigin = new URL(page.url()).origin;
        const requests: string[] = [];
        context.on('request', (request) => requests.push(request.url()));
        await dialog.takeOn.click();
        await expect(myRoles(page).item('cashier')).toHaveCount(1, { timeout: 90_000 });
        expect(requests.filter((url) => new URL(url).origin !== appOrigin)).toEqual([]);
    });

    test('after a reload with the source unreachable, the role is shown with its responsibilities',
        async ({ context, page }) => {
            await routeFixtureSource(context);
            await setUpWallet(page);
            await showDropinRoles(page);
            await takeOnWithNewIdentifier(page, 'cashier');

            await context.unrouteAll({ behavior: 'wait' });
            const blocked: string[] = [];
            await context.route(`https://${FIXTURE_HOST}/**`, async (route) => {
                blocked.push(route.request().url());
                await route.abort('connectionrefused');
            });
            await page.reload();
            await unlock(page);
            const cashier = myRoles(page).item('cashier');
            await expect(cashier.getByText('Not appointed yet', { exact: true })).toBeVisible();
            await expect(cashier.locator('[data-responsibility]')).toHaveText(CASHIER_DUTIES);
            // Coming back from a reload is a new start: no read is kept.
            await expect(rolesPage(page).address).toHaveValue('');
            expect(blocked).toEqual([]);
        });

    test('taking on instructor with an existing identifier, and again', async ({ context, page }) => {
        await routeFixtureSource(context);
        await setUpWallet(page);
        await showDropinRoles(page);
        await takeOnWithNewIdentifier(page, 'cashier');

        let dialog = await startTakingOn(page, 'instructor');
        await expect(dialog.createNew).toBeChecked();
        await expect(dialog.name).toHaveValue('instructor');
        await dialog.selectExisting.check();
        await expect(dialog.existing).toHaveValue(/.+/);
        await expect(dialog.existing.locator('option')).toHaveText(['cashier']);
        await dialog.takeOn.click();
        await expect(dialog.dialog).toHaveCount(0, { timeout: 90_000 });
        const instructor = myRoles(page).item('instructor');
        await expect(instructor.getByText('Identifier: cashier', { exact: true })).toBeVisible();
        await expect(myRoles(page).items.getByRole('heading', { level: 3 })).toHaveText(['instructor', 'cashier']);

        dialog = await startTakingOn(page, 'instructor');
        await dialog.selectExisting.check();
        await dialog.takeOn.click();
        await expect(dialog.status).toHaveText(ALREADY_TAKEN, { timeout: 90_000 });
        await expect(dialog.dialog).toBeVisible();
        await expect(dialog.takeOn).toBeEnabled();
        await expect(dialog.cancel).toBeEnabled();
        await dialog.cancel.click();
        await expect(dialog.dialog).toHaveCount(0);
        await expect(myRoles(page).items).toHaveCount(2);
    });

    test('a role taken on from the second of two publications with one target lists that publication',
        async ({ context, page }) => {
            await routeFixtureSource(context, { edit: editSource((document) => {
                document.publications.push({ ...document.publications[1], name: 'dropin-again' });
            }) });
            await setUpWallet(page);
            await openAddress(page, FIXTURE_HOST);
            const again = rolesPage(page).card('dropin-again');
            await again.getByRole('button', { name: 'Show roles' }).click({ timeout: 90_000 });
            await again.getByRole('button', { name: 'Take on cashier', exact: true }).click();
            const dialog = takeOnDialog(page, 'cashier');
            await dialog.takeOn.click();
            await expect(dialog.dialog).toHaveCount(0, { timeout: 90_000 });
            await expect(myRoles(page).item('cashier').getByText('dropin-again at mad.example.test', { exact: true }))
                .toBeVisible();
        });

    test('the name offered is the role\'s, numbered when that alias is taken', async ({ context, page }) => {
        await routeFixtureSource(context);
        await setUpWallet(page);
        await showDropinRoles(page);
        await takeOnWithNewIdentifier(page, 'cashier');
        const dialog = await startTakingOn(page, 'cashier');
        await expect(dialog.createNew).toBeChecked();
        await expect(dialog.selectExisting).not.toBeChecked();
        await expect(dialog.name).toHaveValue('cashier 2');
    });

    test('an identifier name that is taken is shown in the dialog, which stays open', async ({ context, page }) => {
        await routeFixtureSource(context);
        await setUpWallet(page);
        await showDropinRoles(page);
        await takeOnWithNewIdentifier(page, 'cashier');
        const dialog = await startTakingOn(page, 'instructor');
        await dialog.name.fill('cashier');
        await dialog.takeOn.click();
        await expect(dialog.status).toHaveText('Identifier alias \'cashier\' already exists.', { timeout: 90_000 });
        await expect(dialog.dialog).toBeVisible();
        await expect(dialog.name).toHaveValue('cashier');
        await expect(myRoles(page).items).toHaveCount(1);
    });

    test('both dialog buttons are disabled while the wallet works', async ({ context, page }) => {
        await routeFixtureSource(context);
        await setUpWallet(page);
        await showDropinRoles(page);
        const dialog = await startTakingOn(page, 'cashier');
        await dialog.dialog.evaluate((root) => {
            const buttons = Array.from(root.querySelectorAll('button[data-action]')) as HTMLButtonElement[];
            const seen: boolean[][] = [];
            (window as unknown as { takeOnButtonsSeen: boolean[][] }).takeOnButtonsSeen = seen;
            new MutationObserver(() => seen.push(buttons.map((button) => button.disabled)))
                .observe(root, { subtree: true, attributes: true, attributeFilter: ['disabled'] });
        });
        await dialog.takeOn.click();
        await expect(myRoles(page).item('cashier')).toHaveCount(1, { timeout: 90_000 });
        const seen = await page.evaluate(() => (window as unknown as { takeOnButtonsSeen: boolean[][] }).takeOnButtonsSeen);
        expect(seen[0]).toEqual([true, true]);
    });

    test('after the runtime is discarded in the background, taking on a role needs the address opened again',
        async ({ context, page }) => {
            await routeFixtureSource(context);
            await setUpWallet(page);
            await showDropinRoles(page);
            await sendToBackgroundFor31Seconds(page);
            await unlock(page);
            // The page keeps no read across a discarded runtime: there is no Take on to press.
            const roles = rolesPage(page);
            await expect(roles.address).toHaveValue('');
            await expect(roles.cards).toHaveCount(0);
            // A fresh runtime, as after the discard, refuses to take on a role from a read it has not made.
            await startRequest(page, 'vaults.open', { vaultId: 'my-wallet', passcode: 'roles-pass-1' });
            expect((await finishRequest(page)).ok).toBe(true);
            await startRequest(page, 'roles.takeOn', {
                vaultId: 'my-wallet', address: FIXTURE_HOST, target: DROPIN_CLASS, publication: 'dropin-class',
                role: 'cashier', identifier: { mode: 'new', alias: 'cashier' },
            });
            expect(await finishRequest(page)).toEqual({ ok: false, code: 'CONFLICT', message: OPEN_AGAIN });
        });

    test('Remove deletes the role, and it stays gone after a reload', async ({ context, page }) => {
        await routeFixtureSource(context);
        await setUpWallet(page);
        await showDropinRoles(page);
        await takeOnWithNewIdentifier(page, 'cashier');
        const cashier = myRoles(page).item('cashier');
        await cashier.getByRole('button', { name: 'Remove cashier', exact: true }).click();
        await expect(myRoles(page).items).toHaveCount(0, { timeout: 90_000 });
        await expect(myRoles(page).region.getByText('No roles yet. Open an address to see what you can take on.'))
            .toBeVisible();
        await expect(myRoles(page).region.getByRole('heading', { name: 'My roles' })).toBeFocused();
        await page.reload();
        await unlock(page);
        await expect(myRoles(page).region.getByText('No roles yet. Open an address to see what you can take on.'))
            .toBeVisible();
        // The identifier stays.
        await page.getByRole('navigation', { name: 'Vault navigation' })
            .getByRole('link', { name: 'Identifiers', exact: true }).click();
        await expect(page.getByRole('link', { name: 'cashier', exact: true })).toBeVisible();
    });

    test('a vault made before roles existed opens with no roles and can take one on', async ({ context, page }) => {
        await routeFixtureSource(context);
        await setUpWallet(page);
        // An upgrade is a new start of the app over the vault as it was. Before this change a vault had no frol.
        // and fdef. stores: remove them while no runtime has the vault open.
        await page.reload();
        await expect(page.getByRole('heading', { name: 'Open My Wallet' })).toBeVisible({ timeout: 240_000 });
        const removed = await page.evaluate(async () => {
            const names = ['frol.', 'fdef.'].map((store) => `IDBMap/@fortweb/fortweb-vault-my-wallet:${store}`);
            for (const name of names) {
                await new Promise<void>((resolve, reject) => {
                    const request = indexedDB.deleteDatabase(name);
                    request.onsuccess = () => resolve();
                    request.onerror = () => reject(request.error);
                    request.onblocked = () => reject(new Error(`${name} is still open`));
                });
            }
            const left = (await indexedDB.databases()).map((database) => database.name);
            return names.filter((name) => left.includes(name));
        });
        expect(removed).toEqual([]);
        await page.getByLabel('Passcode').fill('roles-pass-1');
        await page.getByRole('button', { name: 'Open', exact: true }).click();
        await expect(page.getByRole('heading', { name: 'Roles', exact: true, level: 1 })).toBeVisible({ timeout: 180_000 });
        await expect(myRoles(page).region.getByText('No roles yet. Open an address to see what you can take on.'))
            .toBeVisible();
        await showDropinRoles(page);
        await takeOnWithNewIdentifier(page, 'cashier');
    });

    test('a roles store the wallet cannot read still shows the Roles page', async ({ context, page }) => {
        await routeFixtureSource(context);
        await setUpWallet(page);
        await showDropinRoles(page);
        await takeOnWithNewIdentifier(page, 'cashier');
        // After a reload no runtime has the vault open: replace the stored role record (frol.) with bytes that are
        // not JSON, so the store cannot rebuild it and roles.list fails.
        await page.reload();
        await expect(page.getByRole('heading', { name: 'Open My Wallet' })).toBeVisible({ timeout: 240_000 });
        const replaced = await page.evaluate(async () => {
            const done = <T>(request: IDBRequest<T>) => new Promise<T>((resolve, reject) => {
                request.onsuccess = () => resolve(request.result);
                request.onerror = () => reject(request.error);
            });
            const database = await done(indexedDB.open('IDBMap/@fortweb/fortweb-vault-my-wallet:frol.'));
            const [storeName] = Array.from(database.objectStoreNames);
            const store = () => database.transaction(storeName, 'readwrite').objectStore(storeName);
            const records = await done(store().get('__records__'));
            if (typeof records !== 'string') throw new Error(`unexpected records: ${typeof records}`);
            // WebDBer keeps a sub-database as JSON: hex key -> hex value.
            const notJson = Array.from(new TextEncoder().encode('{not json'))
                .map((byte) => byte.toString(16).padStart(2, '0')).join('');
            const parsed = JSON.parse(records) as Record<string, string>;
            for (const key of Object.keys(parsed)) parsed[key] = notJson;
            await done(store().put(JSON.stringify(parsed), '__records__'));
            database.close();
            return Object.keys(parsed).length;
        });
        expect(replaced).toBe(1);
        await page.getByLabel('Passcode').fill('roles-pass-1');
        await page.getByRole('button', { name: 'Open', exact: true }).click();
        await expect(page.getByRole('heading', { name: 'Roles', exact: true, level: 1 })).toBeVisible({ timeout: 180_000 });
        const roles = rolesPage(page);
        await expect(roles.address).toBeEditable();
        await expect(myRoles(page).region.getByText('My roles could not be read.', { exact: true })).toBeVisible();
        await expect(myRoles(page).items).toHaveCount(0);
        // The address box still works.
        await showDropinRoles(page);
        await expect(roles.cards).toHaveCount(3);
    });
});
