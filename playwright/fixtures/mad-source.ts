import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import type { BrowserContext } from '@playwright/test';

// The fixture source of python/fixtures/mad served at https://mad.example.test/, with no real server:
// Playwright's context.route also answers fetches made by the wallet's worker.

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const FIXTURES = path.join(REPO, 'python', 'fixtures', 'mad');
const LIVE_SOURCE = 'https://mad.seriouscoder.one/source.json';
export const FIXTURE_HOST = 'mad.example.test';
export const FIXTURE_SOURCE = `https://${FIXTURE_HOST}/source.json`;
const DEFINITION_PATH = /^\/definitions\/(E[A-Za-z0-9_-]{43})\.json$/;

export interface FixtureSourceOptions {
    /** false: answer without allowing the wallet's origin, so the wallet may not read it. */
    cors?: boolean;
    /** Serve this definition with one byte changed. */
    tamper?: string;
    /** Serve a source document of 1,000,001 bytes. */
    oversize?: boolean;
}

export function fixtureSourceDocument(): Buffer {
    const text = readFileSync(path.join(FIXTURES, 'source.json'), 'utf8');
    if (!text.includes(`"${LIVE_SOURCE}"`)) {
        throw new Error('fixture source.json no longer names the live source');
    }
    return Buffer.from(text.replace(`"${LIVE_SOURCE}"`, `"${FIXTURE_SOURCE}"`), 'utf8');
}

/** One byte changed: the first lowercase letter after the SAID's own occurrence becomes uppercase. */
export function tampered(bytes: Buffer, said: string): Buffer {
    const copy = Buffer.from(bytes);
    const start = copy.indexOf(said) + said.length;
    for (let i = Math.max(start, 0); i < copy.length; i += 1) {
        if (copy[i] >= 0x61 && copy[i] <= 0x7a) {
            copy[i] -= 0x20;
            return copy;
        }
    }
    throw new Error(`cannot tamper with ${said}`);
}

function definitions(): Map<string, Buffer> {
    const dir = path.join(FIXTURES, 'compact');
    return new Map(readdirSync(dir).filter((name) => name.endsWith('.json'))
        .map((name) => [name.slice(0, -'.json'.length), readFileSync(path.join(dir, name))]));
}

export async function routeFixtureSource(context: BrowserContext, options: FixtureSourceOptions = {}): Promise<void> {
    // Oversize: the valid document padded with trailing spaces, so its size is its only fault.
    const valid = fixtureSourceDocument();
    const document = options.oversize
        ? Buffer.concat([valid, Buffer.alloc(1_000_001 - valid.length, 0x20)])
        : valid;
    const served = definitions();
    if (options.tamper !== undefined) {
        const original = served.get(options.tamper);
        if (original === undefined) {
            throw new Error(`no fixture definition ${options.tamper}`);
        }
        served.set(options.tamper, tampered(original, options.tamper));
    }
    // route.fulfill adds access-control-allow-origin: <request origin> when the header is missing
    // (playwright-core server/network.js _maybeAddCorsHeaders), so a source that does not allow the wallet is
    // answered with an origin that is not the wallet's: to the browser that is the same refusal.
    const headers: Record<string, string> = {
        'content-type': 'application/json',
        'access-control-allow-origin': options.cors === false ? 'https://not-the-wallet.example.test' : '*',
    };
    await context.route(`https://${FIXTURE_HOST}/**`, async (route) => {
        const { pathname } = new URL(route.request().url());
        if (pathname === '/source.json') {
            await route.fulfill({ status: 200, headers, body: document });
            return;
        }
        const said = DEFINITION_PATH.exec(pathname)?.[1];
        const body = said === undefined ? undefined : served.get(said);
        if (body === undefined) {
            await route.fulfill({ status: 404, headers, body: '' });
            return;
        }
        await route.fulfill({ status: 200, headers, body });
    });
}
