import { expect, test } from '@playwright/test';
import { FIXTURE_HOST, routeFixtureSource } from './fixtures/mad-source.js';
import {
    CLASS_ATTENDED, setUpWallet, openStartPage, NOT_AN_ADDRESS, ONE_DEFINITION, MARKUP, editSource, heldSource,
    rolesPage, openAddress,
} from './fixtures/roles.js';

// The Roles page, driven the way a person uses it.

test.describe('Roles page', () => {
    test.setTimeout(480_000);

    test('after setting up a wallet the Roles page is shown, with the empty state', async ({ page }) => {
        await setUpWallet(page);
        await expect(page).toHaveURL(/#\/vaults\/my-wallet\/roles$/);
        const roles = rolesPage(page);
        await expect(roles.address).toHaveAttribute('placeholder', 'example.org');
        await expect(roles.open).toBeEnabled();
        await expect(roles.main.getByRole('heading', { name: 'My roles', exact: true })).toBeVisible();
        await expect(roles.main.getByText('No roles yet. Open an address to see what you can take on.', { exact: true }))
            .toBeVisible();
        const nav = page.getByRole('navigation', { name: 'Vault navigation' });
        await expect(nav.getByRole('link').first()).toHaveText('Roles');
        await expect(nav.getByRole('link', { name: 'Roles', exact: true })).toHaveAttribute('aria-current', 'page');
    });

    test('Open shows the host, the counts and one card per publication', async ({ context, page }) => {
        await routeFixtureSource(context);
        await setUpWallet(page);
        await openAddress(page, FIXTURE_HOST);
        const roles = rolesPage(page);
        await expect(roles.host).toBeVisible({ timeout: 90_000 });
        await expect(roles.summary).toHaveText('3 publications, 26 definitions checked');
        await expect(roles.cards).toHaveCount(3);
        const dropin = roles.card('dropin-class');
        await expect(dropin).toContainText('Test data from gym-bp: the dropin-class selection.');
        await expect(dropin).toContainText('gym-bp test data');
        await expect(dropin.getByRole('listitem')).toHaveText(['gym', 'test-data']);
        await expect(roles.status).toHaveText('');
    });

    test('Enter in the address field opens it', async ({ context, page }) => {
        await routeFixtureSource(context);
        await setUpWallet(page);
        const roles = rolesPage(page);
        await roles.address.fill(FIXTURE_HOST);
        await roles.address.press('Enter');
        await expect(roles.host).toBeVisible({ timeout: 90_000 });
        await expect(roles.summary).toHaveText('3 publications, 26 definitions checked');
        await expect(roles.cards).toHaveCount(3);
    });

    test('Show roles lists the roles and what each is responsible for', async ({ context, page }) => {
        await routeFixtureSource(context);
        await setUpWallet(page);
        await openAddress(page, FIXTURE_HOST);
        const roles = rolesPage(page);
        const dropin = roles.card('dropin-class');
        await dropin.getByRole('button', { name: 'Show roles' }).click();
        await expect(dropin.getByRole('heading', { name: 'Drop-in takes a class', exact: true })).toBeVisible();
        const list = dropin.locator('[data-roles-list] > li');
        await expect(list.getByRole('heading')).toHaveText(['cashier', 'front-desk', 'instructor', 'patron', 'recorder']);
        const instructor = list.filter({ has: page.getByRole('heading', { name: 'instructor', exact: true }) });
        await expect(instructor.locator('[data-responsibility]')).toHaveText([
            'class-attended: relies on enrollment (as seat), attended (as visit); attests class-attended; '
            + 'produces class-attended',
        ]);
        // Each button is named after its role and shows "Take on".
        const buttons = list.getByRole('button', { name: /^Take on / });
        await expect(buttons).toHaveCount(5);
        for (const role of ['cashier', 'front-desk', 'instructor', 'patron', 'recorder']) {
            const button = list.getByRole('button', { name: `Take on ${role}`, exact: true });
            await expect(button).toHaveText('Take on');
            await expect(button).toBeEnabled();
        }
    });

    test('a role with no responsibilities shows no empty list', async ({ page }) => {
        // A read never names a role without a responsibility; the fixture page's data has one ("visitor"). The
        // fixture is opened once the app has started, so that start-up's last render does not replace it.
        await openStartPage(page);
        await page.evaluate(() => { window.location.hash = '#/_fixtures/roles'; });
        const main = page.getByRole('main');
        await main.getByRole('textbox', { name: 'Address' }).fill('example.org');
        await main.getByRole('button', { name: 'Open', exact: true }).click();
        await main.getByRole('button', { name: 'Show roles' }).click();
        const items = main.locator('[data-roles-list] > li');
        const visitor = items.filter({ has: page.getByRole('heading', { name: 'visitor', exact: true }) });
        await expect(visitor).toHaveCount(1);
        await expect(visitor.getByRole('list')).toHaveCount(0);
        const frontDesk = items.filter({ has: page.getByRole('heading', { name: 'front-desk', exact: true }) });
        await expect(frontDesk.getByRole('list')).toHaveCount(1);
    });

    test('Show roles and Hide roles keep aria-expanded correct', async ({ context, page }) => {
        await routeFixtureSource(context);
        await setUpWallet(page);
        await openAddress(page, FIXTURE_HOST);
        const dropin = rolesPage(page).card('dropin-class');
        const toggle = dropin.getByRole('button', { name: 'Show roles' });
        await expect(toggle).toHaveAttribute('aria-expanded', 'false');
        await expect(dropin.getByRole('heading', { name: 'Drop-in takes a class' })).toBeHidden();
        await toggle.click();
        const hide = dropin.getByRole('button', { name: 'Hide roles' });
        await expect(hide).toHaveAttribute('aria-expanded', 'true');
        await expect(dropin.getByRole('heading', { name: 'Drop-in takes a class' })).toBeVisible();
        await hide.click();
        await expect(dropin.getByRole('button', { name: 'Show roles' })).toHaveAttribute('aria-expanded', 'false');
        await expect(dropin.getByRole('heading', { name: 'Drop-in takes a class' })).toBeHidden();
    });

    test('a failed read shows its sentence in the status line and announces it', async ({ context, page }) => {
        await setUpWallet(page);
        const roles = rolesPage(page);
        const cases: Array<[string, (() => Promise<unknown>) | null, string]> = [
            ['http://x', null, NOT_AN_ADDRESS],
            [FIXTURE_HOST, () => routeFixtureSource(context, { cors: false }),
                'Could not read mad.example.test: it cannot be reached, or it does not allow wallets to read it yet.'],
            ['down.example.test', () => context.route('https://down.example.test/**',
                (route) => route.abort('connectionrefused')),
            'Could not read down.example.test: it cannot be reached, or it does not allow wallets to read it yet.'],
            [FIXTURE_HOST, () => routeFixtureSource(context, { sourceAnswer: 'missing' }),
                'mad.example.test did not return a valid source.'],
            [FIXTURE_HOST, () => routeFixtureSource(context, { oversize: true }),
                'mad.example.test publishes more than the wallet reads in one go.'],
        ];
        for (const [address, route, message] of cases) {
            await context.unrouteAll({ behavior: 'wait' });
            await route?.();
            await openAddress(page, address);
            await expect(roles.status).toHaveText(message, { timeout: 90_000 });
            await expect(roles.announcer).toHaveText(message);
            await expect(roles.open).toBeEnabled();
            await expect(roles.cards).toHaveCount(0);
        }
    });

    test('markup in a publication name is shown as text', async ({ context, page }) => {
        const dialogs: string[] = [];
        page.on('dialog', (dialog) => {
            dialogs.push(dialog.message());
            void dialog.dismiss();
        });
        await routeFixtureSource(context, { edit: editSource((document) => {
            document.publications[1].name = MARKUP;
        }) });
        await setUpWallet(page);
        await openAddress(page, FIXTURE_HOST);
        const roles = rolesPage(page);
        await expect(roles.cards).toHaveCount(3, { timeout: 90_000 });
        await expect(roles.main.getByText(MARKUP, { exact: true })).toBeVisible();
        await expect(roles.main.locator('img')).toHaveCount(0);
        expect(dialogs).toEqual([]);
    });

    test('a source whose text is not well-formed Unicode is not a valid source', async ({ context, page }) => {
        // JSON.stringify writes an unpaired surrogate as the escape \ud83d: valid JSON, but text that cannot be
        // stored or sent as UTF-8.
        await routeFixtureSource(context, { edit: editSource((document) => {
            document.publications[1].name = 'dropin-class \ud83d';
        }) });
        await setUpWallet(page);
        await openAddress(page, FIXTURE_HOST);
        const roles = rolesPage(page);
        await expect(roles.status).toHaveText('mad.example.test did not return a valid source.', { timeout: 90_000 });
        await expect(roles.cards).toHaveCount(0);
    });

    test('Open is disabled while a read runs, and a second submit does nothing', async ({ context, page }) => {
        const held = heldSource();
        await routeFixtureSource(context, { holdSource: held.option });
        await setUpWallet(page);
        const roles = rolesPage(page);
        await openAddress(page, FIXTURE_HOST);
        try {
            await held.arrived;
            await expect(roles.open).toBeDisabled();
            await expect(roles.status).toHaveText('Reading...');
            // Enter does not submit a form whose only submit button is disabled, so the submit is sent directly:
            // only the page's own guard can stop it.
            const prevented = await roles.main.locator('[data-roles-form]').evaluate((form) => {
                const event = new Event('submit', { cancelable: true });
                form.dispatchEvent(event);
                return event.defaultPrevented;
            });
            expect(prevented).toBe(true);
            await expect(roles.status).toHaveText('Reading...');
            expect(held.count()).toBe(1);
        } finally {
            held.release();
        }
        await expect(roles.summary).toHaveText('3 publications, 26 definitions checked', { timeout: 90_000 });
        await expect(roles.open).toBeEnabled();
        expect(held.count()).toBe(1);
    });

    test('a new read clears the previous result as it starts', async ({ context, page }) => {
        await routeFixtureSource(context);
        await setUpWallet(page);
        const roles = rolesPage(page);
        await openAddress(page, FIXTURE_HOST);
        await expect(roles.cards).toHaveCount(3, { timeout: 90_000 });

        await context.unrouteAll({ behavior: 'wait' });
        const held = heldSource();
        await routeFixtureSource(context, { holdSource: held.option });
        await openAddress(page, FIXTURE_HOST);
        try {
            await held.arrived;
            await expect(roles.status).toHaveText('Reading...');
            await expect(roles.host).toHaveCount(0);
            await expect(roles.summary).toHaveCount(0);
            await expect(roles.cards).toHaveCount(0);
        } finally {
            held.release();
        }
        await expect(roles.cards).toHaveCount(3, { timeout: 90_000 });
    });

    test('a source with one publication and one definition uses the singular', async ({ context, page }) => {
        await routeFixtureSource(context, { edit: editSource((document) => {
            document.publications = [document.publications[1]];
        }) });
        await setUpWallet(page);
        const roles = rolesPage(page);
        await openAddress(page, FIXTURE_HOST);
        await expect(roles.summary).toHaveText('1 publication, 19 definitions checked', { timeout: 90_000 });

        await context.unrouteAll({ behavior: 'wait' });
        await routeFixtureSource(context, { edit: editSource((document) => {
            document.publications = [{ ...document.publications[0], target: ONE_DEFINITION, name: 'single' }];
        }) });
        await openAddress(page, FIXTURE_HOST);
        await expect(roles.summary).toHaveText('1 publication, 1 definition checked', { timeout: 90_000 });
    });

    test('a publication whose definition does not match shows its sentence and no roles', async ({ context, page }) => {
        await routeFixtureSource(context, { tamper: CLASS_ATTENDED });
        await setUpWallet(page);
        const roles = rolesPage(page);
        await openAddress(page, FIXTURE_HOST);
        await expect(roles.cards).toHaveCount(3, { timeout: 90_000 });
        for (const card of await roles.cards.all()) {
            await expect(card.getByText('A definition from mad.example.test does not match its identifier.',
                { exact: true })).toBeVisible();
            await expect(card.getByRole('button', { name: 'Show roles' })).toHaveCount(0);
            await expect(card.getByRole('button', { name: 'Take on' })).toHaveCount(0);
        }
    });

    test('a publication past the read\'s limit shows its sentence and no roles', async ({ context, page }) => {
        // Fifty served definitions that do not exist use up the definitions one read fetches; dropin-class, listed
        // after them, still needs its own and is cut off.
        const fakes = Array.from({ length: 50 }, (_, i) => `EA${String(i).padStart(42, 'x')}`);
        await routeFixtureSource(context, { edit: editSource((document) => {
            document.serves.push({ said: fakes[0], refers: fakes.slice(1) });
            for (const said of fakes.slice(1)) document.serves.push({ said, refers: [] });
            document.publications = [
                { ...document.publications[0], target: fakes[0], name: 'fifty-missing' },
                document.publications[1],
            ];
        }) });
        await setUpWallet(page);
        const roles = rolesPage(page);
        await openAddress(page, FIXTURE_HOST);
        const dropin = roles.card('dropin-class');
        await expect(dropin.getByText('mad.example.test publishes more than the wallet reads in one go.',
            { exact: true })).toBeVisible({ timeout: 90_000 });
        await expect(dropin.getByRole('button', { name: 'Show roles' })).toHaveCount(0);
        await expect(roles.card('fifty-missing').getByText(
            'A definition from mad.example.test does not match its identifier.', { exact: true })).toBeVisible();
    });

    test('what a source says about itself is shown as unverified', async ({ context, page }) => {
        await routeFixtureSource(context, { edit: editSource((document) => {
            document.by = 'EOperatorOfTheSource';
            document.oobi = 'https://mad.example.test/oobi/EOperatorOfTheSource';
            document.introductions = [{}];
            document.catalogs = [{}, {}];
            document.serves.push({ said: 'not-a-said', refers: [] });
            document.publications.push({ name: 'no target' });
            document.publications[1].by = 'EPublisherOfDropin';
        }) });
        await setUpWallet(page);
        const roles = rolesPage(page);
        await openAddress(page, FIXTURE_HOST);
        await expect(roles.cards).toHaveCount(3, { timeout: 90_000 });
        const source = roles.main.locator('[data-roles-source]');
        await expect(source.getByText('Unverified: says it is run by EOperatorOfTheSource', { exact: true })).toBeVisible();
        await expect(source.getByText('Unverified: gives the OOBI https://mad.example.test/oobi/EOperatorOfTheSource',
            { exact: true })).toBeVisible();
        await expect(source.getByRole('link')).toHaveCount(0);
        await expect(source.getByText(
            'It introduces 1 other source and lists 2 catalogs. The wallet does not follow them yet.',
            { exact: true })).toBeVisible();
        await expect(source.getByText('2 entries were ignored.', { exact: true })).toBeVisible();
        await expect(roles.card('dropin-class').getByText('Unverified: says it is published by EPublisherOfDropin',
            { exact: true })).toBeVisible();
        await expect(roles.card('class-brick').getByText(/Unverified/)).toHaveCount(0);
    });

    test('one introduction, one catalog and one ignored entry use the singular', async ({ context, page }) => {
        await routeFixtureSource(context, { edit: editSource((document) => {
            document.introductions = [{}];
            document.catalogs = [{}];
            document.serves.push({ said: 'not-a-said', refers: [] });
        }) });
        await setUpWallet(page);
        const roles = rolesPage(page);
        await openAddress(page, FIXTURE_HOST);
        await expect(roles.cards).toHaveCount(3, { timeout: 90_000 });
        const source = roles.main.locator('[data-roles-source]');
        await expect(source.getByText(
            'It introduces 1 other source and lists 1 catalog. The wallet does not follow them yet.',
            { exact: true })).toBeVisible();
        await expect(source.getByText('1 entry was ignored.', { exact: true })).toBeVisible();
    });

    test('several introductions use the plural', async ({ context, page }) => {
        await routeFixtureSource(context, { edit: editSource((document) => {
            document.introductions = [{}, {}];
            document.catalogs = [];
        }) });
        await setUpWallet(page);
        const roles = rolesPage(page);
        await openAddress(page, FIXTURE_HOST);
        await expect(roles.cards).toHaveCount(3, { timeout: 90_000 });
        await expect(roles.main.locator('[data-roles-source]').getByText(
            'It introduces 2 other sources and lists 0 catalogs. The wallet does not follow them yet.',
            { exact: true })).toBeVisible();
    });

    test('the last read stays on the page: coming back shows it as it was left', async ({ context, page }) => {
        await routeFixtureSource(context);
        await setUpWallet(page);
        const roles = rolesPage(page);
        await openAddress(page, FIXTURE_HOST);
        await expect(roles.cards).toHaveCount(3, { timeout: 90_000 });
        await roles.card('dropin-class').getByRole('button', { name: 'Show roles' }).click();
        const nav = page.getByRole('navigation', { name: 'Vault navigation' });
        await nav.getByRole('link', { name: 'Identifiers', exact: true }).click();
        await expect(page.getByRole('heading', { name: 'Local Identifiers', exact: true })).toBeVisible();
        await nav.getByRole('link', { name: 'Roles', exact: true }).click();
        await expect(roles.address).toHaveValue(FIXTURE_HOST);
        await expect(roles.summary).toHaveText('3 publications, 26 definitions checked');
        await expect(roles.cards).toHaveCount(3);
        await expect(roles.card('dropin-class').getByRole('button', { name: 'Hide roles' }))
            .toHaveAttribute('aria-expanded', 'true');
        await expect(roles.card('class-brick').getByRole('button', { name: 'Show roles' }))
            .toHaveAttribute('aria-expanded', 'false');
    });
});
