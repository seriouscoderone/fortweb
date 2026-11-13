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
const NOT_THE_WALLET = 'https://not-the-wallet.example.test';
const DEFINITION_PATH = /^\/definitions\/(E[A-Za-z0-9_-]{43})\.json$/;

export interface FixtureSourceOptions {
    /** false: answer without allowing the wallet's origin, so the wallet may not read it. */
    cors?: boolean;
    /** false: answer the definitions (only) without allowing the wallet's origin. */
    definitionsCors?: boolean;
    /** The content type the definitions are served with (default application/json). */
    definitionType?: string;
    /** Serve this definition with one byte changed. */
    tamper?: string;
    /** Answer this definition, which the source serves, with 404. */
    missing?: string;
    /** Serve a source document of 1,000,001 bytes. */
    oversize?: boolean;
    /** Serve every definition as 1,000,001 bytes. */
    oversizeDefinitions?: boolean;
    /** Answer source.json with 404, with a 200 text/html page, or with a 302 to another address on the host. */
    sourceAnswer?: 'missing' | 'html' | 'redirect';
    /** Hold the answer to source.json: `arrived` is called when its request comes in, the answer waits for `release`. */
    holdSource?: { arrived(): void; release: Promise<void> };
    /** Hold the answer to this definition in the same way. */
    holdDefinition?: { said: string; arrived(): void; release: Promise<void> };
    /** Change the parsed source document before it is served (e.g. a publication's name, or the source's `by`). */
    edit?(document: Record<string, unknown>): void;
    /** More definitions to serve, by SAID (made with `definition`). */
    extra?: Map<string, Buffer>;
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

// Blake3-256 (one input, default mode), for definitions a test makes: a SAID is the digest of the bytes with the
// SAID's place filled with 44 '#' (mad-discovery src/said.ts).

const IV = [0x6A09E667, 0xBB67AE85, 0x3C6EF372, 0xA54FF53A, 0x510E527F, 0x9B05688C, 0x1F83D9AB, 0x5BE0CD19];
const PERMUTATION = [2, 6, 3, 10, 7, 0, 4, 13, 1, 11, 12, 5, 9, 14, 15, 8];
const CHUNK_START = 1;
const CHUNK_END = 2;
const PARENT = 4;
const ROOT = 8;

function compress(cv: number[], block: number[], counter: number, length: number, flags: number): number[] {
    const v = [...cv, IV[0], IV[1], IV[2], IV[3], counter >>> 0, Math.floor(counter / 2 ** 32), length, flags];
    const rotr = (x: number, n: number) => ((x >>> n) | (x << (32 - n))) >>> 0;
    const g = (a: number, b: number, c: number, d: number, x: number, y: number) => {
        v[a] = (v[a] + v[b] + x) >>> 0; v[d] = rotr(v[d] ^ v[a], 16);
        v[c] = (v[c] + v[d]) >>> 0; v[b] = rotr(v[b] ^ v[c], 12);
        v[a] = (v[a] + v[b] + y) >>> 0; v[d] = rotr(v[d] ^ v[a], 8);
        v[c] = (v[c] + v[d]) >>> 0; v[b] = rotr(v[b] ^ v[c], 7);
    };
    let m = block;
    for (let round = 0; round < 7; round += 1) {
        g(0, 4, 8, 12, m[0], m[1]); g(1, 5, 9, 13, m[2], m[3]); g(2, 6, 10, 14, m[4], m[5]); g(3, 7, 11, 15, m[6], m[7]);
        g(0, 5, 10, 15, m[8], m[9]); g(1, 6, 11, 12, m[10], m[11]); g(2, 7, 8, 13, m[12], m[13]); g(3, 4, 9, 14, m[14], m[15]);
        m = PERMUTATION.map((i) => m[i]);
    }
    return Array.from({ length: 8 }, (_, i) => (v[i] ^ v[i + 8]) >>> 0);
}

function words(bytes: Buffer, start: number): number[] {
    const block = Buffer.alloc(64);
    bytes.copy(block, 0, start, Math.min(start + 64, bytes.length));
    return Array.from({ length: 16 }, (_, i) => block.readUInt32LE(i * 4));
}

/** The chaining value of a subtree of `input` whose first chunk is chunk number `chunk`. */
function node(input: Buffer, chunk: number, root: boolean): number[] {
    if (input.length <= 1024) {
        let cv = IV;
        const blocks = Math.max(1, Math.ceil(input.length / 64));
        for (let b = 0; b < blocks; b += 1) {
            const last = b === blocks - 1;
            const flags = (b === 0 ? CHUNK_START : 0) | (last ? CHUNK_END | (root ? ROOT : 0) : 0);
            cv = compress(cv, words(input, b * 64), chunk, last ? input.length - b * 64 : 64, flags);
        }
        return cv;
    }
    const chunks = Math.ceil(input.length / 1024);
    const left = 2 ** Math.floor(Math.log2(chunks - 1));
    return compress(IV, [...node(input.subarray(0, left * 1024), chunk, false),
        ...node(input.subarray(left * 1024), chunk + left, false)], 0, 64, PARENT | (root ? ROOT : 0));
}

export function blake3(input: Buffer): Buffer {
    const out = Buffer.alloc(32);
    node(input, 0, true).forEach((word, i) => out.writeUInt32LE(word, i * 4));
    return out;
}

/** A definition with its SAID: `body` after "d", compact; returns [SAID, served bytes]. */
export function definition(body: Record<string, unknown>): [string, Buffer] {
    const fill = '#'.repeat(44);
    const text = JSON.stringify({ d: fill, ...body });
    const said = `E${Buffer.concat([Buffer.alloc(1), blake3(Buffer.from(text, 'utf8'))]).toString('base64url').slice(1)}`;
    return [said, Buffer.from(text.replace(fill, said), 'utf8')];
}

function definitions(): Map<string, Buffer> {
    const dir = path.join(FIXTURES, 'compact');
    return new Map(readdirSync(dir).filter((name) => name.endsWith('.json'))
        .map((name) => [name.slice(0, -'.json'.length), readFileSync(path.join(dir, name))]));
}

/** What the wallet has asked the fixture source for. */
export interface FixtureSourceRequests {
    /** The SAID of every definition requested so far, in the order the requests arrived. */
    definitions: string[];
}

/** The bytes padded with trailing spaces to 1,000,001 bytes, so that their size is their only fault. */
function oversized(bytes: Buffer): Buffer {
    return Buffer.concat([bytes, Buffer.alloc(1_000_001 - bytes.length, 0x20)]);
}

export async function routeFixtureSource(
    context: BrowserContext, options: FixtureSourceOptions = {},
): Promise<FixtureSourceRequests> {
    let valid = fixtureSourceDocument();
    if (options.edit) {
        const parsed = JSON.parse(valid.toString('utf8')) as Record<string, unknown>;
        options.edit(parsed);
        valid = Buffer.from(JSON.stringify(parsed), 'utf8');
    }
    const document = options.oversize ? oversized(valid) : valid;
    const served = definitions();
    for (const [said, body] of options.extra ?? []) {
        served.set(said, body);
    }
    const requests: FixtureSourceRequests = { definitions: [] };
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
        'access-control-allow-origin': options.cors === false ? NOT_THE_WALLET : '*',
    };
    await context.route(`https://${FIXTURE_HOST}/**`, async (route) => {
        const { pathname } = new URL(route.request().url());
        if (pathname === '/source.json') {
            if (options.holdSource) {
                options.holdSource.arrived();
                await options.holdSource.release;
            }
            if (options.sourceAnswer === 'missing') {
                await route.fulfill({ status: 404, headers, body: '' });
            } else if (options.sourceAnswer === 'html') {
                await route.fulfill({ status: 200, headers: { ...headers, 'content-type': 'text/html' },
                    body: '<!doctype html><title>x</title>' });
            } else if (options.sourceAnswer === 'redirect') {
                await route.fulfill({ status: 302, headers: { ...headers, location: '/moved/source.json' }, body: '' });
            } else {
                await route.fulfill({ status: 200, headers, body: document });
            }
            return;
        }
        const said = DEFINITION_PATH.exec(pathname)?.[1];
        if (said !== undefined) {
            requests.definitions.push(said);
            if (said === options.holdDefinition?.said) {
                options.holdDefinition.arrived();
                await options.holdDefinition.release;
            }
        }
        const body = said === undefined || said === options.missing ? undefined : served.get(said);
        if (body === undefined) {
            await route.fulfill({ status: 404, headers, body: '' });
            return;
        }
        const refused = options.definitionsCors === false ? { 'access-control-allow-origin': NOT_THE_WALLET } : {};
        const type = options.definitionType === undefined ? {} : { 'content-type': options.definitionType };
        await route.fulfill({ status: 200, headers: { ...headers, ...refused, ...type },
            body: options.oversizeDefinitions ? oversized(body) : body });
    });
    return requests;
}
