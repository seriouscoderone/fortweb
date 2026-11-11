// Client for native phone capabilities exposed by the Fortoid Android host.
//
// Fortoid installs `window.bridge` (a WebMessageListener object) for the
// trusted app origin. Requests and responses use its native_request /
// native_response envelopes. In a plain browser or on iOS there is no such
// bridge and isNativeAvailable() is false.

const REQUEST_TYPE = "native_request";
const RESPONSE_TYPE = "native_response";

export class NativeCallError extends Error {
    readonly code: string;

    constructor(code: string, message: string) {
        super(message);
        this.name = "NativeCallError";
        this.code = code;
    }
}

export interface QrScanResult {
    text: string;
    format: string;
    isOobi: boolean;
    oobiAid?: string;
    truncated: boolean;
}

interface PendingCall {
    resolve(result: unknown): void;
    reject(error: NativeCallError): void;
}

interface NativeResponse {
    type: string;
    id: string;
    ok: boolean;
    result?: unknown;
    error?: { code?: string; message?: string };
}

const pending = new Map<string, PendingCall>();
let sequence = 0;
let listening = false;

function nativeBridge(): AndroidBridgeHandler | null {
    if (typeof window === "undefined") return null;
    const bridge = window.bridge;
    if (!bridge || typeof bridge.postMessage !== "function" || typeof bridge.addEventListener !== "function") {
        return null;
    }
    return bridge;
}

function onBridgeMessage(event: { data: unknown }): void {
    let response: NativeResponse;
    try {
        response = JSON.parse(String(event.data)) as NativeResponse;
    } catch {
        return;
    }
    if (!response || response.type !== RESPONSE_TYPE) return;

    const call = pending.get(response.id);
    if (!call) return;
    pending.delete(response.id);

    if (response.ok) {
        call.resolve(response.result);
    } else {
        call.reject(new NativeCallError(
            response.error?.code || "failed",
            response.error?.message || "Native call failed.",
        ));
    }
}

export function isNativeAvailable(): boolean {
    return nativeBridge() !== null;
}

export function callNative<T>(capability: string, params: Record<string, unknown> = {}): Promise<T> {
    const bridge = nativeBridge();
    if (!bridge) {
        return Promise.reject(new NativeCallError("unavailable", "Native capabilities are not available here."));
    }
    if (!listening) {
        bridge.addEventListener("message", onBridgeMessage);
        listening = true;
    }

    sequence += 1;
    const id = `fw${sequence}-${Date.now().toString(36)}`;
    return new Promise<T>((resolve, reject) => {
        pending.set(id, { resolve: (result) => resolve(result as T), reject });
        bridge.postMessage(JSON.stringify({ type: REQUEST_TYPE, id, capability, params }));
    });
}

export function scanQrCode(prompt?: string): Promise<QrScanResult> {
    return callNative<QrScanResult>("qr.scan", prompt ? { prompt } : {});
}
