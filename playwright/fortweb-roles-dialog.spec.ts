import { expect, test } from '@playwright/test';
import { definition, routeFixtureSource } from './fixtures/mad-source.js';
import {
    setUpWallet, rolesPage, takeOnDialog, showDropinRoles, myRoles, startTakingOn, takeOnWithNewIdentifier,
    recordAnnouncements, announced, editSource,
} from './fixtures/roles.js';

// The dialog that takes on a role.

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

    test('the name offered is numbered when it differs from a taken alias only by a joiner', async ({ context, page }) => {
        // A publication whose one role is "cashier" followed by a zero-width joiner, which does not show.
        const [rr, rrBytes] = definition({ kind: 'rr', alias: 'till', role: 'cashier\u200d' });
        const [selection, selectionBytes] = definition({ kind: 'selection', name: 'Joined', rrs: [rr] });
        await routeFixtureSource(context, {
            extra: new Map([[rr, rrBytes], [selection, selectionBytes]]),
            edit: editSource((document) => {
                document.serves.push({ said: selection, refers: [rr] }, { said: rr, refers: [] });
                document.publications.push({ ...document.publications[1], target: selection, name: 'joined' });
            }),
        });
        await setUpWallet(page);
        await showDropinRoles(page);
        await takeOnWithNewIdentifier(page, 'cashier');
        const joined = rolesPage(page).card('joined');
        await joined.getByRole('button', { name: 'Show roles' }).click();
        await joined.locator('[data-roles-list] button').click();
        const dialog = page.locator('[role="dialog"]:not([inert])');
        await expect(dialog.getByRole('textbox', { name: 'Name' })).toHaveValue('cashier\u200d 2');
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
