import { expect, test } from '@playwright/test';
import { FIXTURE_HOST, FIXTURE_SOURCE, routeFixtureSource } from './fixtures/mad-source.js';
import {
    CLASS_BRICK, DROPIN_CLASS, MEMBER_CLASS, CLASS_ATTENDED, PAID_ADMISSION, setUpWallet, openStartPage,
    type TestWindow, startRequest, finishRequest, requestWithin, readSource, byTarget, NOT_AN_ADDRESS,
    ONE_DEFINITION, MARKUP, editSource, heldSource, rolesPage, openAddress,
} from './fixtures/roles.js';

// sources.read against the real runtime. The fixture source is answered by context.route; the runtime is called
// through a bridge made the way app/app/main.ts makes its own (same module, worker and config URLs).

test.describe('sources.read', () => {
    test.setTimeout(480_000);

    test('reads the fixture source: host, publications, checked definitions and roles', async ({ context, page }) => {
        await routeFixtureSource(context);
        await setUpWallet(page);
        const outcome = await readSource(page, FIXTURE_HOST);
        expect(outcome.ok).toBe(true);
        if (!outcome.ok) return;
        const { result } = outcome;
        expect(result.source.host).toBe(FIXTURE_HOST);
        expect(result.source.address).toBe(FIXTURE_SOURCE);
        expect(result.checked).toBe(26);
        expect(result.dropped).toEqual({ entries: 0, publications: 0 });
        expect(result.publications.map((p) => p.name)).toEqual(['class-brick', 'dropin-class', 'member-class']);
        expect(result).not.toHaveProperty('definitions');
        const dropin = byTarget(outcome)[DROPIN_CLASS];
        expect(dropin.problem).toBeNull();
        expect(dropin.title).toBe('Drop-in takes a class');
        expect(dropin.roles.map((r) => r.role)).toEqual(['cashier', 'front-desk', 'instructor', 'patron', 'recorder']);
        expect(dropin.roles.find((r) => r.role === 'instructor')?.responsibilities).toEqual([{
            said: CLASS_ATTENDED,
            alias: 'class-attended',
            reliesOn: [
                { said: 'EEnCEVn-d6pEqEbX2tdKpaZnuldiha_x4l0FZdeQXhU-', alias: 'enrollment', kind: 'authorization', as: 'seat' },
                { said: 'EJd1iQ-VgrOUblb-gE6m4jYNC3Iua5t8nJS9KK6yph69', alias: 'attended', kind: 'fact', as: 'visit' },
            ],
            attests: [{ said: 'EHC_ESsWWD0Y2UKuPcdAfd8LzZ1hPSA2Z9afFtFsOpjC', alias: 'class-attended', kind: 'fact' }],
            outcome: { said: 'EHC_ESsWWD0Y2UKuPcdAfd8LzZ1hPSA2Z9afFtFsOpjC', alias: 'class-attended', kind: 'fact' },
        }]);
    });

    test('reads the fixture source before any wallet is set up', async ({ context, page }) => {
        await routeFixtureSource(context);
        await openStartPage(page);
        const outcome = await readSource(page, FIXTURE_HOST);
        expect(outcome.ok && outcome.result.publications.map((p) => [p.name, p.problem])).toEqual([
            ['class-brick', null], ['dropin-class', null], ['member-class', null],
        ]);
        expect(outcome.ok && outcome.result.checked).toBe(26);
        // Still no wallet: the start page is what the person sees.
        await expect(page.getByRole('heading', { name: 'Set up your wallet' })).toBeVisible();
    });

    test('a source that does not allow the wallet\'s origin may not be read yet', async ({ context, page }) => {
        await routeFixtureSource(context, { cors: false });
        await setUpWallet(page);
        expect(await readSource(page, FIXTURE_HOST)).toEqual({
            ok: false, code: 'NETWORK_ERROR', message: 'mad.example.test does not allow wallets to read it yet.',
        });
    });

    test('a tampered definition marks exactly the publications that use it', async ({ context, page }) => {
        await routeFixtureSource(context, { tamper: CLASS_ATTENDED });
        await setUpWallet(page);
        const all = byTarget(await readSource(page, FIXTURE_HOST));
        for (const target of [CLASS_BRICK, DROPIN_CLASS, MEMBER_CLASS]) {
            expect([all[target].problem, all[target].roles]).toEqual(['mismatch', []]);
        }

        // A definition only dropin-class uses: the other two keep their roles.
        await context.unrouteAll({ behavior: 'wait' });
        await routeFixtureSource(context, { tamper: PAID_ADMISSION });
        const some = byTarget(await readSource(page, FIXTURE_HOST));
        expect([some[DROPIN_CLASS].problem, some[DROPIN_CLASS].roles]).toEqual(['mismatch', []]);
        for (const target of [CLASS_BRICK, MEMBER_CLASS]) {
            expect(some[target].problem).toBeNull();
            expect(some[target].roles.length).toBeGreaterThan(0);
        }
    });

    test('a source document over 1,000,000 bytes is refused', async ({ context, page }) => {
        await routeFixtureSource(context, { oversize: true });
        await setUpWallet(page);
        expect(await readSource(page, FIXTURE_HOST)).toEqual({
            ok: false, code: 'BAD_RESPONSE', message: 'mad.example.test publishes more than the wallet reads in one go.',
        });
    });

    test('a read stops at its byte budget when every definition is over 1,000,000 bytes', async ({ context, page }) => {
        const requests = await routeFixtureSource(context, { oversizeDefinitions: true });
        await openStartPage(page);
        const outcome = await readSource(page, FIXTURE_HOST);
        // Each fetch gives up at 1,000,001 bytes and four of them spend the read's 4,000,000 bytes: the first four
        // of class-brick's definitions (in SAID order) are requested, the other 22 the source serves never are.
        expect(requests.definitions).toEqual([
            'ECru8k3CerlcJF0Khe0EOubRaA4U4APPGqOdvkzNR0L1',
            CLASS_BRICK,
            'EEfawOeTW43nlaZXFYBxjw5XQuLkbm6Jdw-L1EbAXviK',
            'EEnCEVn-d6pEqEbX2tdKpaZnuldiha_x4l0FZdeQXhU-',
        ]);
        // All three publications use at least one of those four, so each is a mismatch and nothing was checked.
        expect(outcome.ok && outcome.result.publications.map((p) => [p.name, p.problem, p.roles])).toEqual([
            ['class-brick', 'mismatch', []], ['dropin-class', 'mismatch', []], ['member-class', 'mismatch', []],
        ]);
        expect(outcome.ok && outcome.result.checked).toBe(0);
    });

    for (const [answer, code, message] of [
        ['missing', 'BAD_RESPONSE', 'mad.example.test did not return a valid source.'],
        ['html', 'BAD_RESPONSE', 'mad.example.test did not return a valid source.'],
        // The read refuses redirects. A redirect answered by route.fulfill cannot be followed by any fetch in Chromium
        // (checked: cors and no-cors, same-host and other-host targets, the target is never requested), so the
        // no-cors probe fails as well and the host counts as unreachable. A real server's redirect is followed by the
        // probe and gives "does not allow wallets to read it yet" (a known, accepted limitation).
        ['redirect', 'NETWORK_ERROR', 'Could not reach mad.example.test.'],
    ] as const) {
        test(`a source that answers ${answer === 'missing' ? '404' : answer === 'html' ? '200 text/html' : '302'}`,
            async ({ context, page }) => {
                await routeFixtureSource(context, { sourceAnswer: answer });
                await openStartPage(page);
                expect(await readSource(page, FIXTURE_HOST)).toEqual({ ok: false, code, message });
            });
    }

    test('a host that cannot be reached', async ({ context, page }) => {
        await context.route('https://down.example.test/**', (route) => route.abort('connectionrefused'));
        await setUpWallet(page);
        expect(await readSource(page, 'down.example.test')).toEqual({
            ok: false, code: 'NETWORK_ERROR', message: 'Could not reach down.example.test.',
        });
    });

    test('a slow read does not hold up other requests on the same bridge', async ({ context, page }) => {
        let arrived!: () => void;
        const sourceRequested = new Promise<void>((resolve) => { arrived = resolve; });
        let release!: () => void;
        const released = new Promise<void>((resolve) => { release = resolve; });
        await routeFixtureSource(context, { holdSource: { arrived, release: released } });
        await openStartPage(page);
        await startRequest(page, 'sources.read', { address: FIXTURE_HOST });
        try {
            await sourceRequested; // the read is now waiting for the source's answer
            expect(await requestWithin(page, 'vaults.list', 10_000)).toEqual({ ok: true });
            expect(await requestWithin(page, 'settings.get', 10_000)).toEqual({ ok: true });
            expect(await page.evaluate(() => (window as unknown as TestWindow).rolesPendingReadSettled)).toBe(false);
        } finally {
            release();
        }
        const outcome = await finishRequest(page);
        expect(outcome.ok && outcome.result.checked).toBe(26);
    });

    test('an address the wallet cannot open', async ({ page }) => {
        await setUpWallet(page);
        expect(await readSource(page, 'http://x')).toEqual({
            ok: false, code: 'VALIDATION', message: 'That is not an address the wallet can open.',
        });
    });
});

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
                'mad.example.test does not allow wallets to read it yet.'],
            ['down.example.test', () => context.route('https://down.example.test/**',
                (route) => route.abort('connectionrefused')), 'Could not reach down.example.test.'],
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
