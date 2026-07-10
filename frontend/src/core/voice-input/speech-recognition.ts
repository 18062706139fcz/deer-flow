export type SpeechRecognitionErrorCode =
  | "aborted"
  | "audio-capture"
  | "bad-grammar"
  | "language-not-supported"
  | "network"
  | "no-speech"
  | "not-allowed"
  | "phrases-not-supported"
  | "service-not-allowed";

export type SpeechRecognitionErrorKind =
  | "cancelled"
  | "microphone_unavailable"
  | "permission_denied"
  | "unsupported_language"
  | "network"
  | "no_speech"
  | "unknown";

export type SpeechRecognitionConstructor = new () => BrowserSpeechRecognition;

export type SpeechRecognitionEventLike = {
  results: SpeechRecognitionResultListLike;
};

export type SpeechRecognitionErrorEventLike = {
  error?: SpeechRecognitionErrorCode | string;
};

export type BrowserSpeechRecognition = {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  maxAlternatives: number;
  onend: (() => void) | null;
  onerror: ((event: SpeechRecognitionErrorEventLike) => void) | null;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
};

type SpeechRecognitionWindow = Window &
  typeof globalThis & {
    SpeechRecognition?: SpeechRecognitionConstructor;
    webkitSpeechRecognition?: SpeechRecognitionConstructor;
  };

export type SpeechRecognitionAlternativeLike = {
  transcript?: string;
};

export type SpeechRecognitionResultLike = {
  0?: SpeechRecognitionAlternativeLike;
  isFinal: boolean;
  length: number;
};

export type SpeechRecognitionResultListLike = {
  [index: number]: SpeechRecognitionResultLike | undefined;
  length: number;
};

export function getSpeechRecognitionConstructor(
  value: unknown = globalThis,
): SpeechRecognitionConstructor | null {
  const maybeWindow = value as Partial<SpeechRecognitionWindow>;
  return (
    maybeWindow.SpeechRecognition ?? maybeWindow.webkitSpeechRecognition ?? null
  );
}

export function getSpeechRecognitionLanguage(locale: string): string {
  if (locale.toLowerCase().startsWith("zh")) {
    return "zh-CN";
  }
  return "en-US";
}

export function readSpeechRecognitionTranscript(
  results: SpeechRecognitionResultListLike,
): { finalText: string; interimText: string; text: string } {
  let finalText = "";
  let interimText = "";

  for (const result of Array.from(
    { length: results.length },
    (_, index) => results[index],
  )) {
    const transcript = result?.[0]?.transcript ?? "";
    if (result?.isFinal) {
      finalText += transcript;
    } else {
      interimText += transcript;
    }
  }

  return {
    finalText: normalizeSpeechTranscript(finalText),
    interimText: normalizeSpeechTranscript(interimText),
    text: normalizeSpeechTranscript(`${finalText}${interimText}`),
  };
}

export function appendSpeechTranscript(baseText: string, transcript: string) {
  const cleanTranscript = normalizeSpeechTranscript(transcript);
  if (!cleanTranscript) {
    return baseText;
  }

  const cleanBase = baseText.trimEnd();
  if (!cleanBase) {
    return cleanTranscript;
  }

  return `${cleanBase} ${cleanTranscript}`;
}

export function normalizeSpeechTranscript(value: string) {
  return value.replace(/\s+/g, " ").trim();
}

export function mapSpeechRecognitionError(
  error: SpeechRecognitionErrorCode | string | undefined,
): SpeechRecognitionErrorKind {
  switch (error) {
    case "aborted":
      return "cancelled";
    case "audio-capture":
      return "microphone_unavailable";
    case "not-allowed":
    case "service-not-allowed":
      return "permission_denied";
    case "language-not-supported":
      return "unsupported_language";
    case "network":
      return "network";
    case "no-speech":
      return "no_speech";
    default:
      return "unknown";
  }
}
