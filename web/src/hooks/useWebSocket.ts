import { useEffect, useRef, useCallback } from 'react';
import { useAuth } from '../context/AuthContext';

type MessageHandler = (data: { type: string; payload: unknown }) => void;

const WS_BASE = (import.meta.env.VITE_API_URL ?? '').replace(/^http/, 'ws');

/** Maximum reconnection attempts before giving up. */
const MAX_RETRIES = 10;

export function useWebSocket(onMessage: MessageHandler) {
  const { token } = useAuth();
  const wsRef = useRef<WebSocket | null>(null);
  const handlerRef = useRef(onMessage);
  handlerRef.current = onMessage;
  const attemptRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const connect = useCallback(() => {
    if (!token) return;

    const url = `${WS_BASE}/ws?token=${encodeURIComponent(token)}`;
    const ws = new WebSocket(url);

    ws.onopen = () => {
      attemptRef.current = 0; // reset on successful connect
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'ping') return; // server heartbeat — ignore
        handlerRef.current(data);
      } catch {
        // ignore malformed messages
      }
    };

    ws.onclose = () => {
      if (wsRef.current !== ws) return; // stale socket
      if (attemptRef.current >= MAX_RETRIES) {
        // Could dispatch a "connection lost" event here
        return;
      }
      const delay =
        Math.min(1000 * 2 ** attemptRef.current, 30_000) +
        Math.random() * 1000;
      attemptRef.current += 1;
      timerRef.current = setTimeout(() => {
        if (wsRef.current === ws) {
          connect();
        }
      }, delay);
    };

    ws.onerror = () => {
      ws.close();
    };

    wsRef.current = ws;
  }, [token]);

  useEffect(() => {
    // Debounce by 50 ms so React 18 StrictMode's rapid
    // mount → unmount → remount cycle cancels the first
    // connect attempt, producing only ONE WebSocket.
    const debounce = setTimeout(() => connect(), 50);
    return () => {
      clearTimeout(debounce);
      const ws = wsRef.current;
      wsRef.current = null;
      if (timerRef.current) clearTimeout(timerRef.current);
      if (ws) ws.close();
    };
  }, [connect]);
}
