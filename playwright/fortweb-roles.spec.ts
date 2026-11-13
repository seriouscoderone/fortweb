import { expect, test } from '@playwright/test';
import { FIXTURE_HOST, FIXTURE_SOURCE, routeFixtureSource } from './fixtures/mad-source.js';
import {
    CLASS_BRICK, DROPIN_CLASS, MEMBER_CLASS, CLASS_ATTENDED, PAID_ADMISSION, setUpWallet, openStartPage,
    type TestWindow, startRequest, finishRequest, requestWithin, readSource, byTarget,
} from './fixtures/roles.js';

// sources.read against the real runtime. The fixture source is answered by context.route; the runtime is called
// through a bridge made the way app/app/main.ts makes its own (same module, worker and config URLs).

const NOT_READABLE = 'Could not read mad.example.test: it cannot be reached, or it does not allow wallets to read it yet.';

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
            ok: false, code: 'NETWORK_ERROR', message: NOT_READABLE,
        });
    });

    test('definitions that may not be read are each asked for once', async ({ context, page }) => {
        const requests = await routeFixtureSource(context, { definitionsCors: false });
        await openStartPage(page);
        const outcome = await readSource(page, FIXTURE_HOST);
        expect(outcome.ok && outcome.result.checked).toBe(0);
        expect(outcome.ok && outcome.result.publications.map((p) => [p.name, p.problem, p.roles])).toEqual([
            ['class-brick', 'unreadable', []], ['dropin-class', 'unreadable', []], ['member-class', 'unreadable', []],
        ]);
        // Every definition the publications need (all 26 the source serves), each exactly once.
        expect(requests.definitions.length).toBe(26);
        expect(new Set(requests.definitions).size).toBe(26);
    });

    test('definitions served as application/schema+json are read', async ({ context, page }) => {
        await routeFixtureSource(context, { definitionType: 'application/schema+json' });
        await openStartPage(page);
        const outcome = await readSource(page, FIXTURE_HOST);
        expect(outcome.ok && outcome.result.checked).toBe(26);
        expect(outcome.ok && outcome.result.publications.map((p) => p.problem)).toEqual([null, null, null]);
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
        // All three publications use at least one of those four, so none can be read and nothing was checked.
        expect(outcome.ok && outcome.result.publications.map((p) => [p.name, p.problem, p.roles])).toEqual([
            ['class-brick', 'unreadable', []], ['dropin-class', 'unreadable', []], ['member-class', 'unreadable', []],
        ]);
        expect(outcome.ok && outcome.result.checked).toBe(0);
    });

    for (const [answer, code, message] of [
        ['missing', 'BAD_RESPONSE', 'mad.example.test did not return a valid source.'],
        ['html', 'BAD_RESPONSE', 'mad.example.test did not return a valid source.'],
        // The read refuses redirects, so its fetch is rejected; a browser does not say why, and the wallet asks
        // nothing more (a request that could tell would follow the redirect): the one sentence for a rejected read.
        ['redirect', 'NETWORK_ERROR', NOT_READABLE],
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
            ok: false, code: 'NETWORK_ERROR',
            message: 'Could not read down.example.test: it cannot be reached, or it does not allow wallets to read it yet.',
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
