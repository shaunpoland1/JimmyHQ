// The shapes the speech routes answer with. See docs/speech.md.

export type SpeechEngine = "off" | "local" | "parakeet" | "endpoint";

/** Parakeet's model files: what the Voice settings show before and while they are downloaded. */
export type SpeechDownload = {
  /** Already in the download cache; null when it cannot be told (the add-on is missing). */
  present: boolean | null;
  /** The size of the download, rounded. */
  megabytes: number | null;
  /** Where it comes from. */
  host: string;
  /** The owner's "download now" is under way. */
  preparing: boolean;
  /** Why the last "download now" failed, and what to do; null when it did not. */
  problem: string | null;
};

export type SpeechLimits = {
  /** Longest recording the server accepts, in seconds. */
  max_seconds: number;
  /** Largest upload the server accepts, in bytes. */
  max_bytes: number;
  /** How long a recording waits for a busy local model before it is told to try again. */
  busy_wait_seconds: number;
};

export type SpeechStatus = {
  engine: SpeechEngine;
  /** Usable right now. Does not account for the hosted-caller rule. */
  ready: boolean;
  /** Why not, and what to do; null when ready. */
  problem: string | null;
  /** True when audio would leave this computer. */
  hosted: boolean;
  /** Null when the engine is off. */
  location: "local" | "hosted" | null;
  /**
   * Stricter than `location`: the words certainly stay on the owner's own machines (Whisper,
   * Parakeet, or a speech server the owner said is theirs or that is this computer). Null
   * when off; missing from an older server, which counts as not known.
   */
  own_machines?: boolean | null;
  model: string | null;
  /** The chosen engine's model is already loaded in the server process (Whisper or Parakeet). */
  warm: boolean;
  /** Parakeet only; null for every other engine. */
  download: SpeechDownload | null;
  /** Whether a speech server key is saved. The key itself is never sent. */
  key_set: boolean;
  /** Callers allowed to use a hosted engine, trimmed and lower-cased. */
  hosted_callers: string[];
  /** Default language, lower-cased; empty means detect. */
  language: string;
  limits: SpeechLimits;
  accepted_types: string[];
};

export type TranscriptWord = { start: number; end: number; word: string };

export type TranscriptSegment = {
  start: number;
  end: number;
  text: string;
  /** Present only when words were asked for. */
  words?: TranscriptWord[];
};

export type Transcript = {
  text: string;
  /** As the engine reports it; empty when unknown. */
  language: string;
  /** Seconds of audio. */
  duration: number;
  segments: TranscriptSegment[];
};

export type KeyResult = { key_set: boolean };

/** The caller name used when a surface does not give its own. */
export const DEFAULT_CALLER = "dictation";
