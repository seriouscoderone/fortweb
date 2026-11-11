interface NativeBridgeMessageHandler {
    postMessage(payload: unknown): void;
}

interface AndroidBridgeHandler {
    postMessage(payload: string): void;
    /** Present on the Fortoid WebMessageListener object; delivers native replies. */
    addEventListener?(type: "message", listener: (event: { data: unknown }) => void): void;
}

interface Window {
    webkit?: {
        messageHandlers?: Record<string, NativeBridgeMessageHandler | undefined>;
    };
    bridge?: AndroidBridgeHandler;
}