import { expect, test, type Page } from '@playwright/test';
import { FIXTURE_HOST, FIXTURE_SOURCE, routeFixtureSource } from './fixtures/mad-source.js';

// sources.read against the real runtime. The fixture source is answered by context.route; the runtime is called
// through a bridge made the way app/app/main.ts makes its own (same module, worker and config URLs).

const CLASS_BRICK = 'EDVEy5suX6WPROMl6vSc5Kc4I86mXxyFsa_xBla4A_dY';
const DROPIN_CLASS = 'EFQ6ngR2kib-dwmW8mizJ4k5IdO0YysG0xSty9ESHDE4';
const MEMBER_CLASS = 'EJfIK_QhDYXcEpciRPUZhVc4ChUEh2EJLAZfn5veZUqL';
const CLASS_ATTENDED = 'EO6exNffu9clQv9czAMDzj9W_CNW3W47yAJhMqYC05Qz'; // an rr of class-brick; all three use it
const PAID_ADMISSION = 'EIjETqo6CwKdjLKiUbve977olj92nuhBoy4NPywpqD1U'; // an rr only dropin-class uses

interface Publication {
    target: string;
    name: string;
    title: string;
    roles: Array<{ role: string; responsibilities: Array<Record<string, unknown>> }>;
    problem: string | null;
}

interface ReadResult {
    source: { address: string; host: string };
    checked: number;
    dropped: { entries: number; publications: number };
    publications: Publication[];
}

type Outcome = { ok: true; result: ReadResult & Record<string, unknown> } | { ok: false; code: string; message: string };

async function setUpWallet(page: Page): Promise<void> {
    await page.goto('/fortweb/app/index.html');
    await expect(page.getByRole('heading', { name: 'Set up your wallet' })).toBeVisible({ timeout: 240_000 });
    await page.getByLabel('Passcode', { exact: true }).fill('roles-pass-1');
    await page.getByLabel('Confirm passcode', { exact: true }).fill('roles-pass-1');
    await page.getByRole('button', { name: 'Create wallet' }).click();
    await expect(page.getByRole('heading', { name: 'Roles', exact: true, level: 1 })).toBeVisible({ timeout: 180_000 });
}

async function openStartPage(page: Page): Promise<void> {
    await page.goto('/fortweb/app/index.html');
    await expect(page.getByRole('heading', { name: 'Set up your wallet' })).toBeVisible({ timeout: 240_000 });
}

interface TestWindow {
    rolesTestBridge?: { request(method: string, params: object): Promise<unknown> };
    rolesPendingRead?: Promise<Outcome>;
    rolesPendingReadSettled?: boolean;
}

/** Starts a request on the test's bridge (made on first use) and keeps its outcome on window.rolesPendingRead. */
async function startRequest(page: Page, method: string, params: object): Promise<void> {
    await page.evaluate(async ([name, args]) => {
        const holder = window as unknown as TestWindow;
        if (!holder.rolesTestBridge) {
            const bridgeModule = '/fortweb/app/runtime/bridge.js';
            const { createRuntimeBridge } = await import(bridgeModule);
            holder.rolesTestBridge = createRuntimeBridge({
                workerUrl: new URL('/fortweb/app/runtime/wallet-worker.py', window.location.href),
                configUrl: new URL('/fortweb/pyscript-ci.toml', window.location.href),
                runtimeOriginContract: null,
            });
        }
        holder.rolesPendingReadSettled = false;
        holder.rolesPendingRead = holder.rolesTestBridge.request(name as string, args as object).then(
            (result) => ({ ok: true, result }) as Outcome,
            (error: { code?: string; message?: string }) =>
                ({ ok: false, code: String(error.code), message: String(error.message) }) as Outcome,
        ).finally(() => { holder.rolesPendingReadSettled = true; });
    }, [method, params] as const);
}

async function finishRequest(page: Page): Promise<Outcome> {
    return page.evaluate(() => (window as unknown as TestWindow).rolesPendingRead as Promise<Outcome>);
}

/** One more request on the same bridge, answered within `withinMs` or reported as code 'STILL_WAITING'. */
async function requestWithin(page: Page, method: string, withinMs: number): Promise<{ ok: boolean; code?: string }> {
    return page.evaluate(async ([name, limit]) => {
        const bridge = (window as unknown as TestWindow).rolesTestBridge;
        if (!bridge) throw new Error('no test bridge yet');
        let timer = 0;
        const late = new Promise<{ ok: boolean; code?: string }>((resolve) => {
            timer = window.setTimeout(() => resolve({ ok: false, code: 'STILL_WAITING' }), limit as number);
        });
        const answered = bridge.request(name as string, {}).then(
            () => ({ ok: true }),
            (error: { code?: string }) => ({ ok: false, code: String(error.code) }),
        );
        try {
            return await Promise.race([answered, late]);
        } finally {
            window.clearTimeout(timer);
        }
    }, [method, withinMs] as const);
}

async function readSource(page: Page, address: string): Promise<Outcome> {
    await startRequest(page, 'sources.read', { address });
    return finishRequest(page);
}

function byTarget(outcome: Outcome): Record<string, Publication> {
    if (!outcome.ok) {
        throw new Error(`sources.read failed: ${outcome.code} ${outcome.message}`);
    }
    return Object.fromEntries(outcome.result.publications.map((p) => [p.target, p]));
}

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

const NOT_AN_ADDRESS = 'That is not an address the wallet can open.';
const ONE_DEFINITION = 'ECru8k3CerlcJF0Khe0EOubRaA4U4APPGqOdvkzNR0L1'; // served, refers to nothing
const MARKUP = '<img src=x onerror=alert(1)>';

type SourceDocument = Record<string, unknown> & { publications: Array<Record<string, unknown>>; serves: unknown[] };

function editSource(change: (document: SourceDocument) => void): (document: Record<string, unknown>) => void {
    return (document) => change(document as SourceDocument);
}

/** A held answer to source.json: `arrived` resolves when its request comes in; `release()` lets it through. */
function heldSource(): { option: { arrived(): void; release: Promise<void> }; arrived: Promise<void>; release(): void; count(): number } {
    let requests = 0;
    let arrive!: () => void;
    const arrived = new Promise<void>((resolve) => { arrive = resolve; });
    let release!: () => void;
    const released = new Promise<void>((resolve) => { release = resolve; });
    return {
        option: { arrived() { requests += 1; arrive(); }, release: released },
        arrived,
        release,
        count: () => requests,
    };
}

function rolesPage(page: Page) {
    const main = page.getByRole('main');
    return {
        main,
        address: main.getByRole('textbox', { name: 'Address' }),
        open: main.getByRole('button', { name: 'Open', exact: true }),
        status: main.locator('[data-roles-status]'),
        announcer: page.locator('#fw-live-region'),
        host: main.getByRole('heading', { level: 2, name: FIXTURE_HOST, exact: true }),
        summary: main.locator('[data-roles-summary]'),
        cards: main.getByRole('article'),
        card: (name: string) => main.getByRole('article', { name, exact: true }),
    };
}

async function openAddress(page: Page, address: string): Promise<void> {
    const roles = rolesPage(page);
    await roles.address.fill(address);
    await roles.open.click();
}

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

// Taking on a role, My roles, and the last read kept by the app.

const NOT_APPOINTED_SENTENCE = 'Nobody has granted this identifier the role. This wallet cannot hold grants yet.';
const OPEN_AGAIN = 'Open the address again before taking on a role.';
const ALREADY_TAKEN = 'This identifier has already taken on that role.';
const CASHIER_DUTIES = [
    'payment-received: relies on remittance-submitted (as remittance); attests payment-received; '
    + 'produces payment-received',
    'paid-admission: relies on payment-received (as payment); produces admission',
];

/** Opens the fixture source and shows the roles of dropin-class ("Drop-in takes a class"). */
async function showDropinRoles(page: Page): Promise<void> {
    const roles = rolesPage(page);
    await openAddress(page, FIXTURE_HOST);
    await roles.card('dropin-class').getByRole('button', { name: 'Show roles' }).click({ timeout: 90_000 });
}

function myRoles(page: Page) {
    const region = page.getByRole('main').getByRole('region', { name: 'My roles' });
    return {
        region,
        items: region.getByRole('listitem').filter({ has: page.getByRole('heading', { level: 3 }) }),
        item: (role: string) => region.getByRole('listitem')
            .filter({ has: page.getByRole('heading', { level: 3, name: role, exact: true }) }),
    };
}

function takeOnDialog(page: Page, role: string) {
    const dialog = page.getByRole('dialog', { name: `Take on ${role}`, exact: true });
    return {
        dialog,
        createNew: dialog.getByRole('radio', { name: 'Create a new identifier' }),
        selectExisting: dialog.getByRole('radio', { name: 'Select an existing identifier' }),
        name: dialog.getByRole('textbox', { name: 'Name' }),
        existing: dialog.getByRole('combobox', { name: 'Identifier' }),
        takeOn: dialog.getByRole('button', { name: 'Take on', exact: true }),
        cancel: dialog.getByRole('button', { name: 'Cancel', exact: true }),
        status: dialog.locator('[data-take-on-status]'),
    };
}

/** Clicks "Take on" for `role` in dropin-class (its roles shown) and returns the dialog. */
async function startTakingOn(page: Page, role: string) {
    await rolesPage(page).card('dropin-class').getByRole('button', { name: `Take on ${role}`, exact: true }).click();
    const dialog = takeOnDialog(page, role);
    await expect(dialog.dialog).toBeVisible();
    return dialog;
}

async function takeOnWithNewIdentifier(page: Page, role: string): Promise<void> {
    const dialog = await startTakingOn(page, role);
    await dialog.takeOn.click();
    await expect(dialog.dialog).toHaveCount(0, { timeout: 90_000 });
    await expect(myRoles(page).item(role)).toHaveCount(1);
}

async function unlock(page: Page, passcode = 'roles-pass-1'): Promise<void> {
    await expect(page.getByRole('heading', { name: 'Open My Wallet' })).toBeVisible({ timeout: 240_000 });
    await page.getByLabel('Passcode').fill(passcode);
    await page.getByRole('button', { name: 'Open', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Roles', exact: true, level: 1 })).toBeVisible({ timeout: 180_000 });
}

/** The menu covers most of a phone's screen; the visible strip of the overlay beside it closes it. */
async function closePhoneMenu(page: Page): Promise<void> {
    const viewport = page.viewportSize();
    await page.getByRole('button', { name: 'Close navigation' })
        .click({ position: { x: (viewport?.width ?? 412) - 10, y: (viewport?.height ?? 915) / 2 } });
    await expect(page.getByRole('navigation', { name: 'Vault navigation' })).toBeHidden();
}

async function sendToBackgroundFor31Seconds(page: Page): Promise<void> {
    // As fortweb-wallet-first.spec.ts: hidden, 31 s later visible again; the runtime is discarded.
    await page.evaluate(() => {
        let hidden = false;
        Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
        hidden = true;
        document.dispatchEvent(new Event('visibilitychange'));
        const realNow = Date.now.bind(Date);
        Date.now = () => realNow() + 31_000;
        hidden = false;
        document.dispatchEvent(new Event('visibilitychange'));
    });
}

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
                vaultId: 'my-wallet', address: FIXTURE_HOST, target: DROPIN_CLASS, role: 'cashier',
                identifier: { mode: 'new', alias: 'cashier' },
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
});

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
