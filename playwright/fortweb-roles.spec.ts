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
    await expect(page.getByRole('heading', { name: 'Local Identifiers', exact: true })).toBeVisible({ timeout: 180_000 });
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
