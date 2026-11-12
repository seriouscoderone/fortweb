import { expect, test, type Page } from '@playwright/test';

test.beforeEach(async ({ page }) => {
    // These fixtures exercise shared UI, without starting the wallet worker.
    await page.route('**/app/runtime/bridge.js', async (route) => {
        await route.fulfill({
            contentType: 'text/javascript',
            body: `export function createRuntimeBridge() {
                return {
                    async request(method) {
                        if (method === 'vaults.list') return { vaults: [] };
                        throw new Error('Unexpected runtime request: ' + method);
                    },
                    destroy() {},
                };
            }`,
        });
    });
});

async function openIdentifier(page: Page): Promise<void> {
    await page.goto('/fortweb/app/index.html#/_fixtures/identifiers/empty');
    const opener = page.getByRole('button', { name: 'Add Identifier', exact: true });
    await expect(opener).toBeVisible();
    // Reach the opener through the ordinary keyboard order.
    for (let index = 0; index < 20; index++) {
        await page.keyboard.press('Tab');
        if (await opener.evaluate((element) => element === document.activeElement)) break;
    }
    await expect(opener).toBeFocused();
    await page.keyboard.press('Space');
    const dialog = page.getByRole('dialog', { name: 'Create Identifier', exact: true });
    await expect(dialog).toHaveAttribute('aria-modal', 'true');
    await expect(dialog.getByRole('button', { name: 'Close', exact: true })).toBeFocused();
}

for (const width of [1440, 768, 411, 360]) {
    test(`Create Identifier contains keyboard focus and restores it (${width}px)`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        await openIdentifier(page);
        const dialog = page.getByRole('dialog', { name: 'Create Identifier', exact: true });
        const close = dialog.getByRole('button', { name: 'Close', exact: true });
        const create = dialog.getByRole('button', { name: 'Create', exact: true });

        await page.keyboard.press('Shift+Tab');
        await expect(create).toBeFocused();
        await page.keyboard.press('Tab');
        await expect(close).toBeFocused();
        for (const control of [dialog.getByRole('textbox', { name: 'Alias' }),
            dialog.getByRole('button', { name: 'Cancel', exact: true }), create, close]) {
            await page.keyboard.press('Tab');
            await expect(control).toBeFocused();
        }
        // Inert prevents even programmatic focus from reaching the background.
        await page.getByRole('button', { name: 'Add Identifier', includeHidden: true }).evaluate((element) => {
            (element as HTMLElement).focus();
        });
        await expect(close).toBeFocused();
        await expect.poll(() => page.getByRole('link', { name: 'Settings', includeHidden: true })
            .evaluateAll((elements) => elements.length > 0
                && elements.every((element) => element.closest('[inert]') !== null))).toBe(true);

        await page.keyboard.press('Escape');
        await expect(dialog).toHaveCount(0);
        const opener = page.getByRole('button', { name: 'Add Identifier', exact: true });
        await expect(opener).toBeFocused();
        await page.keyboard.press('Space');
        await expect(close).toBeFocused();
        await page.keyboard.press('Enter');
        await expect(dialog).toHaveCount(0);
        await expect(opener).toBeFocused();
    });
}

test('Create Identifier preserves validation, Cancel and safe fixture submission', async ({ page }) => {
    await openIdentifier(page);
    const dialog = page.getByRole('dialog', { name: 'Create Identifier', exact: true });
    await dialog.getByRole('button', { name: 'Create', exact: true }).click();
    await expect(dialog.getByText('Alias is required.', { exact: true })).toBeVisible();
    await dialog.getByRole('textbox', { name: 'Alias' }).fill('fixture-only');
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(dialog).toHaveCount(0);
    const opener = page.getByRole('button', { name: 'Add Identifier', exact: true });
    await expect(opener).toBeFocused();
    await page.keyboard.press('Space');
    await dialog.getByRole('textbox', { name: 'Alias' }).fill('fixture-only');
    await dialog.getByRole('textbox', { name: 'Alias' }).press('Enter');
    await expect(dialog).toHaveCount(0);
    await expect(opener).toBeFocused();
    await expect(page.getByRole('status')).toContainText('Identifier "fixture-only" created.');
});

test('Add Remote uses the same containment with a different form', async ({ page }) => {
    await page.setViewportSize({ width: 411, height: 845 });
    await page.goto('/fortweb/app/index.html#/_fixtures/remotes/empty');
    const opener = page.getByRole('button', { name: 'Add Remote Identifier', exact: true });
    await opener.focus();
    await page.keyboard.press('Enter');
    const dialog = page.getByRole('dialog', { name: 'Add Remote Identifier', exact: true });
    await expect(dialog.getByRole('button', { name: 'Close', exact: true })).toBeFocused();
    await page.keyboard.press('Shift+Tab');
    await expect(dialog.getByRole('button', { name: 'Resolve OOBI', exact: true })).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(dialog.getByRole('button', { name: 'Close', exact: true })).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(dialog.getByRole('textbox', { name: 'OOBI URL' })).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(dialog.getByRole('textbox', { name: 'Alias' })).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
    await expect(opener).toBeFocused();
});

test('background isolation preserves inert state and the shared announcer', async ({ page }) => {
    await page.goto('/fortweb/app/index.html#/_fixtures/identifiers/empty');
    await page.evaluate(() => {
        const background = document.createElement('button');
        background.textContent = 'Already inert';
        background.id = 'previously-inert';
        background.inert = true;
        document.body.append(background);
    });
    await page.evaluate(async () => {
        const url = '/fortweb/app/ui/core/a11y.js';
        const { announce } = await import(url);
        announce('Before dialog');
    });
    await page.getByRole('button', { name: 'Add Identifier', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Create Identifier', exact: true });
    await expect(dialog.getByRole('button', { name: 'Close', exact: true })).toBeFocused();
    await expect(page.locator('#fw-live-region')).not.toHaveAttribute('inert');
    await page.evaluate(async () => {
        const button = document.createElement('button');
        button.textContent = 'Late background action';
        button.id = 'late-background';
        document.body.append(button);
        const { announce } = await import('/fortweb/app/ui/core/a11y.js');
        announce('Modal feedback');
    });
    await expect(page.locator('#late-background')).toHaveAttribute('inert', '');
    await expect(page.getByRole('status')).toHaveText('Modal feedback');
    // Overlay dismissal stays intentional and does not activate the covered link.
    const originalURL = page.url();
    await page.mouse.click(5, 5);
    await expect(dialog).toHaveCount(0);
    await expect(page).toHaveURL(originalURL);
    await expect(page.getByRole('button', { name: 'Add Identifier', exact: true })).toBeFocused();
    await expect(page.locator('#previously-inert')).toHaveJSProperty('inert', true);
    await expect(page.locator('#late-background')).not.toHaveAttribute('inert');
});

test('generic modal handles dynamic controls, fallback, destroy and reopen', async ({ page }) => {
    await page.goto('/fortweb/app/index.html#/_fixtures/identifiers/empty');
    const opener = page.getByRole('button', { name: 'Add Identifier', exact: true });
    await opener.focus();
    await page.evaluate(async () => {
        const { createModal } = await import('/fortweb/app/ui/composites/modal.js');
        globalThis['modalUnderTest'] = createModal({
            title: 'Generic modal',
            body: `<a href="#">Link</a><input aria-label="Input"><select aria-label="Choice"><option>One</option></select>
                <textarea aria-label="Notes"></textarea><div tabindex="0">Explicit target</div>
                <button disabled>Disabled</button><button hidden>Hidden</button>
                <button style="display:none">Not rendered</button><button tabindex="-1">Not tabbable</button>`,
        });
        globalThis['modalUnderTest'].open();
    });
    const dialog = page.getByRole('dialog', { name: 'Generic modal', exact: true });
    const close = dialog.getByRole('button', { name: 'Close', exact: true });
    await expect(close).toBeFocused();
    await page.keyboard.press('Shift+Tab');
    await expect(dialog.getByText('Explicit target', { exact: true })).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(close).toBeFocused();
    for (const control of [dialog.getByRole('link', { name: 'Link' }),
        dialog.getByRole('textbox', { name: 'Input', exact: true }),
        dialog.getByRole('combobox', { name: 'Choice' }),
        dialog.getByRole('textbox', { name: 'Notes' }), dialog.getByText('Explicit target', { exact: true }), close]) {
        await page.keyboard.press('Tab');
        await expect(control).toBeFocused();
    }
    await dialog.evaluate((element) => {
        const final = document.createElement('button');
        final.textContent = 'Dynamic final';
        element.append(final);
    });
    await page.keyboard.press('Shift+Tab');
    await expect(dialog.getByRole('button', { name: 'Dynamic final' })).toBeFocused();
    await dialog.getByRole('button', { name: 'Dynamic final' }).evaluate((element) => {
        (element as HTMLButtonElement).disabled = true;
    });
    await page.keyboard.press('Tab');
    await expect(close).toBeFocused();
    await dialog.evaluate((element) => element.replaceChildren(document.createTextNode('No controls')));
    await page.keyboard.press('Tab');
    await expect(dialog).toBeFocused();
    await page.keyboard.press('Shift+Tab');
    await expect(dialog).toBeFocused();
    await page.evaluate(() => globalThis['modalUnderTest'].destroy());
    await expect(dialog).toHaveCount(0);
    await expect(opener).toBeFocused();
    await page.evaluate(() => globalThis['modalUnderTest'].open());
    await expect(dialog).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
    await expect(opener).toBeFocused();
    // Removed listeners must not intercept normal background keyboard activation.
    await page.keyboard.press('Space');
    await expect(page.getByRole('dialog', { name: 'Create Identifier', exact: true })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog')).toHaveCount(0);
});

for (const width of [1440, 768, 411, 360]) {
    test(`Roles page is reachable and usable from the keyboard (${width}px)`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        await page.goto('/fortweb/app/index.html#/_fixtures/roles');
        const main = page.getByRole('main');
        const address = main.getByRole('textbox', { name: 'Address' });
        await expect(address).toBeVisible();
        // Reach the field through the ordinary keyboard order.
        for (let index = 0; index < 20; index++) {
            await page.keyboard.press('Tab');
            if (await address.evaluate((element) => element === document.activeElement)) break;
        }
        await expect(address).toBeFocused();
        await page.keyboard.type('example.org');
        await page.keyboard.press('Tab');
        await expect(main.getByRole('button', { name: 'Open', exact: true })).toBeFocused();
        await page.keyboard.press('Enter');
        await expect(main.getByRole('heading', { level: 2, name: 'example.org', exact: true })).toBeVisible();
        await expect(page.locator('#fw-live-region')).toHaveText('example.org: 1 publication, 4 definitions checked');

        const toggle = main.getByRole('button', { name: 'Show roles' });
        for (let index = 0; index < 20; index++) {
            await page.keyboard.press('Tab');
            if (await toggle.evaluate((element) => element === document.activeElement)) break;
        }
        await expect(toggle).toBeFocused();
        await page.keyboard.press('Space');
        const hide = main.getByRole('button', { name: 'Hide roles' });
        await expect(hide).toBeFocused();
        await expect(hide).toHaveAttribute('aria-expanded', 'true');
        const region = main.locator(`#${await hide.getAttribute('aria-controls')}`);
        await expect(region.getByRole('heading', { name: 'Front desk checks a visitor in' })).toBeVisible();
        await expect(region.getByRole('button', { name: 'Take on front-desk', exact: true })).toBeDisabled();
        await expect(region.getByRole('button', { name: 'Take on visitor', exact: true })).toBeDisabled();
        await page.keyboard.press('Enter');
        await expect(main.getByRole('button', { name: 'Show roles' })).toHaveAttribute('aria-expanded', 'false');
        await expect(region).toBeHidden();

        // My roles, with one role taken on: its parts are there, its Remove button is named after the role and the
        // identifier (disabled on the fixture page, as Take on is), and it fits the screen.
        const mine = main.getByRole('region', { name: 'My roles' });
        const role = mine.getByRole('listitem')
            .filter({ has: page.getByRole('heading', { level: 3, name: 'front-desk' }) });
        await expect(role).toHaveCount(1);
        await expect(role.getByText('front-desk-demo at example.org', { exact: true })).toBeVisible();
        await expect(role.getByText('Identifier: front-desk', { exact: true })).toBeVisible();
        await expect(role.getByText('Not appointed yet', { exact: true })).toBeVisible();
        await expect(role.locator('[data-responsibility]')).toHaveCount(1);
        await expect(role.getByRole('button', { name: 'Remove front-desk, identifier front-desk', exact: true }))
            .toBeDisabled();
        const box = await mine.boundingBox();
        expect(box !== null && box.x >= 0 && box.x + box.width <= width).toBe(true);

        // Nothing on the page is wider than the screen.
        expect(await page.evaluate(() => document.documentElement.scrollWidth
            <= document.documentElement.clientWidth)).toBe(true);
    });
}
