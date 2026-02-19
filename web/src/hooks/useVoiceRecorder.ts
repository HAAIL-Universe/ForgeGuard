/**
 * useVoiceRecorder — MediaRecorder-based voice capture with server-side
 * Whisper transcription.
 *
 * Replaces the flaky Web Speech API with:
 *   1. Reliable local audio capture (MediaRecorder → WebM/Opus blob)
 *   2. Upload to POST /transcribe → OpenAI Whisper → text
 *
 * Usage:
 *   const { recording, transcribing, toggle, supported } = useVoiceRecorder({
 *     apiBase: API_BASE,
 *     token,
 *     onTranscript: (text) => setInput(prev => prev ? `${prev} ${text}` : text),
 *     onError: (err) => console.error(err),
 *   });
 */

import { useCallback, useRef, useState } from 'react';

export interface VoiceRecorderOptions {
  /** API base URL (e.g. '' for same-origin). */
  apiBase: string;
  /** JWT bearer token for auth. */
  token: string;
  /** Called with the transcribed text when Whisper returns. */
  onTranscript: (text: string) => void;
  /** Called on any error (permission denied, upload failure, etc.). */
  onError?: (error: string) => void;
}

export interface VoiceRecorderState {
  /** True while the microphone is actively recording. */
  recording: boolean;
  /** True while the audio is being uploaded / transcribed. */
  transcribing: boolean;
  /** Toggle recording on/off.  When stopped, auto-uploads for transcription. */
  toggle: () => void;
  /** True if the browser supports MediaRecorder. */
  supported: boolean;
}

const SUPPORTED =
  typeof window !== 'undefined' &&
  typeof navigator !== 'undefined' &&
  !!navigator.mediaDevices?.getUserMedia &&
  typeof MediaRecorder !== 'undefined';

export function useVoiceRecorder(opts: VoiceRecorderOptions): VoiceRecorderState {
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);

  // Keep opts in a ref so toggle() is stable
  const optsRef = useRef(opts);
  optsRef.current = opts;

  /** Upload the recorded blob to /transcribe and call onTranscript. */
  const uploadAndTranscribe = useCallback(async (blob: Blob) => {
    const { apiBase, token, onTranscript, onError } = optsRef.current;
    if (blob.size === 0) {
      onError?.('Recording was empty');
      return;
    }

    setTranscribing(true);
    try {
      const form = new FormData();
      form.append('file', blob, 'recording.webm');

      const res = await fetch(`${apiBase}/transcribe`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: form,
      });

      if (!res.ok) {
        const errBody = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(errBody.detail || `Transcription failed (${res.status})`);
      }

      const data = await res.json();
      const text = (data.text ?? '').trim();
      if (text) {
        onTranscript(text);
      } else {
        onError?.('No speech detected');
      }
    } catch (err: any) {
      console.error('[voice] transcription error:', err);
      optsRef.current.onError?.(err.message || 'Transcription failed');
    } finally {
      setTranscribing(false);
    }
  }, []);

  /** Stop recording and release the microphone. */
  const stopRecording = useCallback(() => {
    const recorder = recorderRef.current;
    recorderRef.current = null;

    if (recorder && recorder.state !== 'inactive') {
      recorder.stop(); // triggers ondataavailable + onstop
    }

    // Release mic tracks
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;

    setRecording(false);
  }, []);

  /** Request mic access, start MediaRecorder. */
  const startRecording = useCallback(async () => {
    const { onError } = optsRef.current;
    if (!SUPPORTED) {
      onError?.('Voice recording is not supported in this browser');
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      // Prefer WebM/Opus, fall back to whatever the browser supports
      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : MediaRecorder.isTypeSupported('audio/webm')
          ? 'audio/webm'
          : '';

      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : {});
      recorderRef.current = recorder;
      chunksRef.current = [];

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };

      recorder.onstop = () => {
        const blob = new Blob(chunksRef.current, {
          type: mimeType || 'audio/webm',
        });
        chunksRef.current = [];
        uploadAndTranscribe(blob);
      };

      // Request data every 1s for smoother chunking
      recorder.start(1000);
      setRecording(true);
    } catch (err: any) {
      console.error('[voice] mic access error:', err);
      streamRef.current?.getTracks().forEach((t) => t.stop());
      streamRef.current = null;

      if (err.name === 'NotAllowedError') {
        onError?.('Microphone permission denied');
      } else {
        onError?.('Could not access microphone');
      }
    }
  }, [uploadAndTranscribe]);

  /** Toggle recording on/off. */
  const toggle = useCallback(() => {
    if (recording) {
      stopRecording();
    } else {
      startRecording();
    }
  }, [recording, startRecording, stopRecording]);

  return { recording, transcribing, toggle, supported: SUPPORTED };
}
