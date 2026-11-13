import { expect, test, type Page } from '@playwright/test';
import { definition, routeFixtureSource } from './fixtures/mad-source.js';
import {
    setUpWallet, rolesPage, takeOnDialog, showDropinRoles, myRoles, startTakingOn, takeOnWithNewIdentifier,
    recordAnnouncements, announced, watchRuntimeRequests, editSource,
} from './fixtures/roles.js';

// The dialog that takes on a role.

/** From now on, the Enter keydowns the document sees are counted: fresh presses and auto-repeats. */
async function countEnterKeys(page: Page): Promise<void> {
    await page.evaluate(() => {
        const keys = { pressed: 0, repeated: 0 };
        (window as unknown as { enterKeys: typeof keys }).enterKeys = keys;
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') {
                keys[event.repeat ? 'repeated' : 'pressed'] += 1;
            }
        }, true);
    });
}

/** The counts since the last call, which start again from zero. */
async function enterKeys(page: Page): Promise<{ pressed: number; repeated: number }> {
    return page.evaluate(() => {
        const keys = (window as unknown as { enterKeys: { pressed: number; repeated: number } }).enterKeys;
        const counts = { ...keys };
        keys.pressed = 0;
        keys.repeated = 0;
        return counts;
    });
}

/** Enter held down: the press, the first auto-repeat 500 ms later, then `repeats - 1` more, one every 33 ms. */
async function holdEnter(page: Page, repeats: number): Promise<void> {
    await page.keyboard.down('Enter');
    for (let i = 0; i < repeats; i += 1) {
        await page.waitForTimeout(i === 0 ? 500 : 33);
        await page.keyboard.down('Enter'); // the key is already down: the page sees a keydown with repeat: true
    }
    await page.keyboard.up('Enter');
}

test.describe('The take-on dialog', () => {
    test.setTimeout(480_000);

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

    test('the name offered is the role\'s with its white space tidied, numbered on that form', async ({ context, page }) => {
        // A publication whose one role is "cashier" followed by a no-break space.
        const [rr, rrBytes] = definition({ kind: 'rr', alias: 'till', role: 'cashier\u00a0' });
        const [selection, selectionBytes] = definition({ kind: 'selection', name: 'Spaced', rrs: [rr] });
        await routeFixtureSource(context, {
            extra: new Map([[rr, rrBytes], [selection, selectionBytes]]),
            edit: editSource((document) => {
                document.serves.push({ said: selection, refers: [rr] }, { said: rr, refers: [] });
                document.publications.push({ ...document.publications[1], target: selection, name: 'spaced' });
            }),
        });
        await setUpWallet(page);
        await showDropinRoles(page);
        await takeOnWithNewIdentifier(page, 'cashier');
        const spaced = rolesPage(page).card('spaced');
        await spaced.getByRole('button', { name: 'Show roles' }).click();
        await spaced.locator('[data-roles-list] button').click();
        const dialog = page.locator('[role="dialog"]:not([inert])');
        await expect(dialog.getByRole('textbox', { name: 'Name' })).toHaveValue('cashier 2');
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

    test('the dialog opens with focus on the selected choice', async ({ context, page }) => {
        await routeFixtureSource(context);
        await setUpWallet(page);
        await showDropinRoles(page);
        await takeOnWithNewIdentifier(page, 'cashier');
        const dialog = await startTakingOn(page, 'instructor');
        await expect(dialog.createNew).toBeFocused();
        // Whatever was selected in a dialog before, a new one opens with the first choice selected and focused.
        await dialog.selectExisting.check();
        await dialog.cancel.click();
        await expect(dialog.dialog).toHaveCount(0);
        const again = await startTakingOn(page, 'instructor');
        await expect(again.createNew).toBeChecked();
        await expect(again.createNew).toBeFocused();
    });

    test('Enter in the name field takes on the role', async ({ context, page }) => {
        const runtime = await watchRuntimeRequests(page);
        await routeFixtureSource(context);
        await setUpWallet(page);
        await showDropinRoles(page);
        const dialog = await startTakingOn(page, 'cashier');
        await dialog.name.fill('front cashier');
        await dialog.name.press('Enter');
        await expect(dialog.dialog).toHaveCount(0, { timeout: 90_000 });
        await expect(myRoles(page).item('cashier').getByText('Identifier: front cashier', { exact: true }))
            .toBeVisible();
        expect(await runtime.sent('roles.takeOn')).toBe(1);
    });

    test('Enter where the dialog opens takes on the role, with one request', async ({ context, page }) => {
        const runtime = await watchRuntimeRequests(page);
        await routeFixtureSource(context);
        await setUpWallet(page);
        await showDropinRoles(page);
        const dialog = await startTakingOn(page, 'cashier');
        await expect(dialog.createNew).toBeFocused();
        await page.keyboard.press('Enter');
        await expect.poll(() => runtime.sent('roles.takeOn')).toBe(1);
        await expect(dialog.dialog).toHaveCount(0, { timeout: 90_000 });
        await expect(myRoles(page).item('cashier').getByText('Identifier: cashier', { exact: true })).toBeVisible();
        expect(await runtime.sent('roles.takeOn')).toBe(1);
    });

    test('Enter on the second choice, selected with an arrow key, takes on the role with the existing identifier',
        async ({ context, page }) => {
            const runtime = await watchRuntimeRequests(page);
            await routeFixtureSource(context);
            await setUpWallet(page);
            await showDropinRoles(page);
            await takeOnWithNewIdentifier(page, 'cashier');
            const dialog = await startTakingOn(page, 'instructor');
            await expect(dialog.createNew).toBeFocused();
            await page.keyboard.press('ArrowDown');
            await expect(dialog.selectExisting).toBeChecked();
            await expect(dialog.selectExisting).toBeFocused();
            await page.keyboard.press('Enter');
            // Two requests so far: the cashier's, and this one.
            await expect.poll(() => runtime.sent('roles.takeOn')).toBe(2);
            await expect(dialog.dialog).toHaveCount(0, { timeout: 90_000 });
            await expect(myRoles(page).item('instructor').getByText('Identifier: cashier', { exact: true }))
                .toBeVisible();
            expect(await runtime.sent('roles.takeOn')).toBe(2);
        });

    test('Enter on a choice with an empty name shows the sentence and takes on nothing', async ({ context, page }) => {
        const runtime = await watchRuntimeRequests(page);
        await routeFixtureSource(context);
        await setUpWallet(page);
        await showDropinRoles(page);
        const dialog = await startTakingOn(page, 'cashier');
        await expect(dialog.createNew).toBeFocused();
        await dialog.name.fill('');
        await dialog.createNew.focus();
        await recordAnnouncements(page);
        await page.keyboard.press('Enter');
        await expect(dialog.status).toHaveText('Enter a name for the new identifier.');
        await expect(dialog.name).toBeFocused();
        await expect.poll(() => announced(page)).toEqual(['Enter a name for the new identifier.']);
        await expect(dialog.dialog).toBeVisible();
        await expect(myRoles(page).items).toHaveCount(0);
        expect(await runtime.sent('roles.takeOn')).toBe(0);
    });

    test('Enter a second time while the wallet works sends nothing more', async ({ context, page }) => {
        const runtime = await watchRuntimeRequests(page);
        await routeFixtureSource(context);
        await setUpWallet(page);
        await showDropinRoles(page);
        const dialog = await startTakingOn(page, 'cashier');
        await expect(dialog.createNew).toBeFocused();
        // The request is held on its way to the runtime, so the wallet is still working when Enter comes again.
        await runtime.hold();
        try {
            await page.keyboard.press('Enter');
            await expect.poll(() => runtime.held('roles.takeOn')).toBe(1);
            await expect(dialog.status).toHaveText('Taking on the role...');
            await expect(dialog.takeOn).toBeDisabled();
            await expect(dialog.createNew).toBeFocused();
            await page.keyboard.press('Enter');
            expect(await runtime.sent('roles.takeOn')).toBe(1);
        } finally {
            await runtime.release();
        }
        await expect(dialog.dialog).toHaveCount(0, { timeout: 90_000 });
        await expect(myRoles(page).items).toHaveCount(1);
        expect(await runtime.sent('roles.takeOn')).toBe(1);
    });

    test('Enter held on the page\'s "Take on" button opens the dialog and takes on nothing', async ({ context, page }) => {
        const runtime = await watchRuntimeRequests(page);
        await routeFixtureSource(context);
        await setUpWallet(page);
        await showDropinRoles(page);
        await countEnterKeys(page);
        const opener = rolesPage(page).card('dropin-class').getByRole('button', { name: 'Take on cashier', exact: true });
        const dialog = takeOnDialog(page, 'cashier');
        await opener.focus();
        await holdEnter(page, 15);
        // The page saw the key held: the press, then its repeats, which reached the dialog.
        expect(await enterKeys(page)).toEqual({ pressed: 1, repeated: 15 });
        await expect(dialog.dialog).toBeVisible();
        await expect(dialog.status).toHaveText('');
        expect(await runtime.sent('roles.takeOn')).toBe(0);
        await expect(myRoles(page).items).toHaveCount(0);
    });

    test('Enter held on a choice or in the name field sends one request while each take-on is refused',
        async ({ context, page }) => {
            const runtime = await watchRuntimeRequests(page);
            await routeFixtureSource(context);
            await setUpWallet(page);
            await showDropinRoles(page);
            await takeOnWithNewIdentifier(page, 'cashier');
            await countEnterKeys(page);
            for (const where of ['choice', 'name'] as const) {
                const dialog = await startTakingOn(page, 'instructor');
                await expect(dialog.createNew).toBeFocused();
                await dialog.name.fill('cashier');
                await (where === 'choice' ? dialog.createNew : dialog.name).focus();
                await recordAnnouncements(page);
                await enterKeys(page);
                const before = await runtime.sent('roles.takeOn');
                await holdEnter(page, 75); // about 3 s
                expect([where, await enterKeys(page)]).toEqual([where, { pressed: 1, repeated: 75 }]);
                const message = 'Identifier alias \'cashier\' already exists.';
                await expect(dialog.status).toHaveText(message, { timeout: 90_000 });
                await expect.poll(() => announced(page)).toEqual([message]);
                expect([where, (await runtime.sent('roles.takeOn')) - before]).toEqual([where, 1]);
                await dialog.cancel.click();
                await expect(dialog.dialog).toHaveCount(0);
            }
            await expect(myRoles(page).items).toHaveCount(1);
        });

    test('Space and the arrow keys select a choice, and Enter on Cancel or Close closes the dialog',
        async ({ context, page }) => {
            const runtime = await watchRuntimeRequests(page);
            await routeFixtureSource(context);
            await setUpWallet(page);
            await showDropinRoles(page);
            await takeOnWithNewIdentifier(page, 'cashier');
            let dialog = await startTakingOn(page, 'instructor');
            await expect(dialog.createNew).toBeFocused();
            await page.keyboard.press('ArrowDown');
            await expect(dialog.selectExisting).toBeChecked();
            await expect(dialog.existing).toBeVisible();
            await page.keyboard.press('ArrowUp');
            await expect(dialog.createNew).toBeChecked();
            await expect(dialog.name).toBeVisible();
            await dialog.selectExisting.focus();
            await page.keyboard.press('Space');
            await expect(dialog.selectExisting).toBeChecked();
            await dialog.cancel.focus();
            await page.keyboard.press('Enter');
            await expect(dialog.dialog).toHaveCount(0);
            dialog = await startTakingOn(page, 'instructor');
            await expect(dialog.createNew).toBeFocused();
            await dialog.dialog.getByRole('button', { name: 'Close', exact: true }).focus();
            await page.keyboard.press('Enter');
            await expect(dialog.dialog).toHaveCount(0);
            // None of these keys took on a role: the only request was the cashier's.
            await expect(myRoles(page).items).toHaveCount(1);
            expect(await runtime.sent('roles.takeOn')).toBe(1);
        });

    test('Escape closes the dialog and takes on nothing', async ({ context, page }) => {
        await routeFixtureSource(context);
        await setUpWallet(page);
        await showDropinRoles(page);
        const dialog = await startTakingOn(page, 'cashier');
        await expect(dialog.createNew).toBeFocused();
        await page.keyboard.press('Escape');
        await expect(dialog.dialog).toHaveCount(0);
        await expect(myRoles(page).items).toHaveCount(0);
        await page.getByRole('navigation', { name: 'Vault navigation' })
            .getByRole('link', { name: 'Identifiers', exact: true }).click();
        await expect(page.getByRole('heading', { name: 'Local Identifiers', exact: true })).toBeVisible();
        await expect(page.getByRole('link', { name: 'cashier', exact: true })).toHaveCount(0);
    });

    test('an empty name is shown, announced, and focus goes to the name field', async ({ context, page }) => {
        await routeFixtureSource(context);
        await setUpWallet(page);
        await showDropinRoles(page);
        const dialog = await startTakingOn(page, 'cashier');
        await dialog.name.fill('   ');
        await recordAnnouncements(page);
        await dialog.takeOn.click();
        await expect(dialog.status).toHaveText('Enter a name for the new identifier.');
        await expect(dialog.name).toBeFocused();
        await expect.poll(() => announced(page)).toEqual(['Enter a name for the new identifier.']);
        await expect(dialog.dialog).toBeVisible();
    });

    test('a dialog opened just after another closed is the one its controls belong to', async ({ context, page }) => {
        await routeFixtureSource(context);
        await setUpWallet(page);
        await showDropinRoles(page);
        await startTakingOn(page, 'cashier');
        // Cancel and open the next dialog at once, while the first is still fading out.
        await page.evaluate(() => {
            (document.querySelector('[role="dialog"] [data-action="cancel"]') as HTMLButtonElement).click();
            (document.querySelector('button[aria-label="Take on instructor"]') as HTMLButtonElement).click();
        });
        const dialog = takeOnDialog(page, 'instructor');
        await expect(dialog.dialog).toBeVisible();
        await expect(dialog.name).toHaveValue('instructor');
        await dialog.takeOn.click();
        await expect(dialog.dialog).toHaveCount(0, { timeout: 90_000 });
        await expect(myRoles(page).item('instructor')).toHaveCount(1);
    });

    test('a take-on that fails after its dialog was closed is shown on the page and announced once',
        async ({ context, page }) => {
            await routeFixtureSource(context);
            await setUpWallet(page);
            await showDropinRoles(page);
            await takeOnWithNewIdentifier(page, 'cashier');
            const dialog = await startTakingOn(page, 'instructor');
            await dialog.name.fill('cashier');
            await recordAnnouncements(page);
            // The answer cannot arrive within this one task: the dialog is closed while the request is open.
            await page.evaluate(() => {
                (document.querySelector('[role="dialog"] [data-action="submit"]') as HTMLButtonElement).click();
                document.dispatchEvent(
                    new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
            });
            await expect(dialog.dialog).toHaveCount(0);
            const message = 'Identifier alias \'cashier\' already exists.';
            await expect(rolesPage(page).status).toHaveText(message, { timeout: 90_000 });
            await expect.poll(() => announced(page)).toEqual([message]);
            await expect(myRoles(page).items).toHaveCount(1);
        });

    test('the open dialog fits a 320 px wide screen', async ({ context, page }) => {
        await page.setViewportSize({ width: 320, height: 640 });
        await routeFixtureSource(context);
        await setUpWallet(page);
        await showDropinRoles(page);
        await takeOnWithNewIdentifier(page, 'cashier');
        const dialog = await startTakingOn(page, 'instructor');
        await expect(dialog.selectExisting).toBeVisible();
        await expect(dialog.dialog).toHaveClass(/is-visible/);
        expect(await page.evaluate(() => document.documentElement.scrollWidth
            <= document.documentElement.clientWidth)).toBe(true);
        const parts = {
            dialog: dialog.dialog.locator('.lk-dialog'),
            'Create a new identifier': dialog.dialog.locator('label').filter({ hasText: 'Create a new identifier' }),
            'Select an existing identifier': dialog.dialog.locator('label')
                .filter({ hasText: 'Select an existing identifier' }),
            Cancel: dialog.cancel,
            'Take on': dialog.takeOn,
        };
        for (const [name, part] of Object.entries(parts)) {
            const box = await part.boundingBox({ timeout: 10_000 });
            expect(box, name).not.toBeNull();
            expect([name, box!.x >= 0 && box!.x + box!.width <= 320]).toEqual([name, true]);
        }
    });

    test('both dialog buttons are disabled while the wallet works', async ({ context, page }) => {
        await routeFixtureSource(context);
        await setUpWallet(page);
        await showDropinRoles(page);
        /** From now on, the disabled state of [Cancel, Take on] at every change, in the open dialog. */
        const watchButtons = (root: ReturnType<typeof takeOnDialog>['dialog']) => root.evaluate((dialog) => {
            const buttons = Array.from(dialog.querySelectorAll('button[data-action]')) as HTMLButtonElement[];
            const seen: boolean[][] = [];
            (window as unknown as { takeOnButtonsSeen: boolean[][] }).takeOnButtonsSeen = seen;
            new MutationObserver(() => seen.push(buttons.map((button) => button.disabled)))
                .observe(dialog, { subtree: true, attributes: true, attributeFilter: ['disabled'] });
        });
        const seen = () => page.evaluate(
            () => (window as unknown as { takeOnButtonsSeen: boolean[][] }).takeOnButtonsSeen);

        // Until the answer arrives, and as the dialog closes on success, both stay disabled.
        let dialog = await startTakingOn(page, 'cashier');
        await watchButtons(dialog.dialog);
        await dialog.takeOn.click();
        await expect(myRoles(page).item('cashier')).toHaveCount(1, { timeout: 90_000 });
        expect(await seen()).toEqual([[true, true]]);

        // After an error both are enabled again, and not before.
        dialog = await startTakingOn(page, 'instructor');
        await dialog.name.fill('cashier');
        await watchButtons(dialog.dialog);
        await dialog.takeOn.click();
        await expect(dialog.status).toHaveText('Identifier alias \'cashier\' already exists.', { timeout: 90_000 });
        expect(await seen()).toEqual([[true, true], [false, false]]);
        await expect(dialog.takeOn).toBeEnabled();
        await expect(dialog.cancel).toBeEnabled();
    });
});
