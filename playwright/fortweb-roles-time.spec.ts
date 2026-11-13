import { expect, test, type Page } from '@playwright/test';
import { FIXTURE_HOST, routeFixtureSource } from './fixtures/mad-source.js';
import { PAID_ADMISSION, setUpWallet, heldSource, rolesPage, openAddress } from './fixtures/roles.js';

// A request the source does not answer: its time runs out in the real runtime, and the read ends with its sentence
// while the request is still held. These tests wait for the real time limit of a request (10 s).

const COULD_NOT_REACH = `Could not reach ${FIXTURE_HOST}.`;

interface Timing { open: number | null; seen: number | null }

/**
 * From now on, the page notes when the Roles form is submitted (Open) and when an element matching `selector` first
 * holds `text` (or first exists, without `text`), both by the page's own clock. Returns a reader of the time between.
 */
async function timeFromOpen(page: Page, selector: string, text?: string): Promise<() => Promise<Timing>> {
    await page.evaluate(([where, wanted]) => {
        const timing: Timing = { open: null, seen: null };
        (window as unknown as { rolesTiming: Timing }).rolesTiming = timing;
        document.addEventListener('submit', (event) => {
            if ((event.target as Element).matches('[data-roles-form]') && timing.open === null) {
                timing.open = performance.now();
            }
        }, true);
        const look = () => {
            const found = document.querySelector(where as string);
            if (timing.open !== null && timing.seen === null && found
                && (wanted === null || found.textContent === wanted)) {
                timing.seen = performance.now();
            }
        };
        new MutationObserver(look).observe(document.body, { subtree: true, childList: true, characterData: true });
    }, [selector, text ?? null] as const);
    return () => page.evaluate(() => ({ ...(window as unknown as { rolesTiming: Timing }).rolesTiming }));
}

function msFromOpen(timing: Timing): number {
    expect(timing.open).not.toBeNull();
    expect(timing.seen).not.toBeNull();
    return timing.seen! - timing.open!;
}

test.describe('A request the source does not answer', () => {
    test.setTimeout(480_000);

    test('source.json not answered: "Could not reach" after the request\'s time, and the vault stays open',
        async ({ context, page }) => {
            const held = heldSource();
            await routeFixtureSource(context, { holdSource: held.option });
            await setUpWallet(page);
            const roles = rolesPage(page);
            const timing = await timeFromOpen(page, '[data-roles-status]', COULD_NOT_REACH);
            await openAddress(page, FIXTURE_HOST);
            try {
                await held.arrived;
                await expect(roles.status).toHaveText(COULD_NOT_REACH, { timeout: 20_000 });
                const ms = msFromOpen(await timing());
                expect(ms).toBeGreaterThanOrEqual(9_000);
                expect(ms).toBeLessThanOrEqual(16_000);
                expect(held.count()).toBe(1); // asked once, and still held: the wallet stopped waiting on its own
                await expect(roles.open).toBeEnabled();
            } finally {
                held.release();
            }

            // The vault did not close: the Identifiers page opens and lists as usual.
            const nav = page.getByRole('navigation', { name: 'Vault navigation' });
            await nav.getByRole('link', { name: 'Identifiers', exact: true }).click();
            await expect(page.getByRole('heading', { name: 'Local Identifiers', exact: true })).toBeVisible();
            await expect(page.getByRole('heading', { name: 'No Local Identifiers Yet' })).toBeVisible({ timeout: 30_000 });

            // A source that answers is read.
            await nav.getByRole('link', { name: 'Roles', exact: true }).click();
            await context.unrouteAll({ behavior: 'ignoreErrors' });
            await routeFixtureSource(context);
            await openAddress(page, FIXTURE_HOST);
            await expect(roles.summary).toHaveText('3 publications, 26 definitions checked', { timeout: 90_000 });
            await expect(roles.cards).toHaveCount(3);
        });

    test('one definition not answered: its publications cannot be read, the others show their roles',
        async ({ context, page }) => {
            const held = heldSource();
            const requests = await routeFixtureSource(context,
                { holdDefinition: { said: PAID_ADMISSION, ...held.option } });
            await setUpWallet(page);
            const roles = rolesPage(page);
            const timing = await timeFromOpen(page, '[data-roles-summary]');
            await openAddress(page, FIXTURE_HOST);
            try {
                await held.arrived;
                await expect(roles.summary).toHaveText('3 publications, 25 definitions checked', { timeout: 20_000 });
                const ms = msFromOpen(await timing());
                expect(ms).toBeGreaterThanOrEqual(9_000);
                expect(ms).toBeLessThanOrEqual(16_000);
                expect(held.count()).toBe(1);
                expect(requests.definitions.filter((said) => said === PAID_ADMISSION)).toHaveLength(1);
            } finally {
                held.release();
            }
            // Only dropin-class needs paid-admission.
            const dropin = roles.card('dropin-class');
            await expect(dropin.getByText(`A definition from ${FIXTURE_HOST} could not be read.`, { exact: true }))
                .toBeVisible();
            await expect(dropin.getByRole('button', { name: 'Show roles' })).toHaveCount(0);
            for (const name of ['class-brick', 'member-class']) {
                const card = roles.card(name);
                await card.getByRole('button', { name: 'Show roles' }).click();
                await expect(card.locator('[data-roles-list] button').first()).toBeVisible();
                await expect(card.locator('.roles-publication__problem')).toHaveCount(0);
            }
        });
});
