// Transport for the TTS service's /speak endpoint plus the pure time-domain
// lookups the face driver needs. No React, no DOM - so the animation code can
// stay about drawing.

export type viseme_kind = "a" | "e" | "i" | "o" | "u" | "m" | "n" | "x";

export type time_span = {
  start: number;
  end: number;
};

export type viseme_segment = time_span & {
  viseme: viseme_kind;
};

// One sentence piece as the engine actually spoke it, with its audio range.
export type speech_segment = time_span & {
  text: string;
};

export type speech_track = {
  duration: number;
  visemes: viseme_segment[];
  segments: speech_segment[];
  envelope: number[];
  envelope_hz: number;
};

export type spoken_reply = {
  audio_url: string;
  track: speech_track;
};

type speak_response = {
  audio_base64: string;
  media_type: string;
  sample_rate: number;
  duration: number;
  visemes: viseme_segment[];
  segments: speech_segment[];
  envelope: number[];
  envelope_hz: number;
};

// Byte loop over ~800KB, roughly 3ms. Imperative on purpose - this is the
// performance-critical exception, and atob is the only zero-dependency route.
function base64_to_blob(base64: string, media_type: string): Blob {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);

  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }

  return new Blob([bytes], { type: media_type });
}

// The WAV rides along base64-encoded rather than as a second request because
// the duration predictor is stochastic: a separate metadata call would return
// timings for a different synthesis than the audio being played.
export async function fetch_spoken_reply(text: string): Promise<spoken_reply> {
  const response = await fetch("/tts/speak", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ text }),
  });

  if (!response.ok) {
    throw new Error(`TTS 서버 오류 ${response.status}`);
  }

  const data = (await response.json()) as speak_response;

  return {
    audio_url: URL.createObjectURL(
      base64_to_blob(data.audio_base64, data.media_type),
    ),
    track: {
      duration: data.duration,
      visemes: data.visemes,
      segments: data.segments,
      envelope: data.envelope,
      envelope_hz: data.envelope_hz,
    },
  };
}

// Spans are sorted and contiguous and playback advances monotonically, so the
// cursor walks forward 0-2 steps per frame. A backwards jump (replay, or
// stop_speaking resetting currentTime) is the only non-forward case and simply
// restarts the walk rather than carrying a stale index.
export function advance_span_cursor(
  spans: time_span[],
  cursor: number,
  time: number,
): number {
  let index = cursor >= spans.length || time < spans[cursor].start ? 0 : cursor;

  while (index + 1 < spans.length && time >= spans[index].end) {
    index += 1;
  }

  return index;
}

// How far through a sentence the voice has got, 0..1, for the karaoke sweep.
//
// Weighted by loudness rather than by elapsed time so the highlight slows over
// pauses instead of running ahead of the words. The `+ silence_floor` term
// blends in a little plain linear progress, otherwise the sweep would sit
// completely still through a comma and then lurch.
//
// This is an interpolation across the sentence, not per-syllable truth: the
// server knows exactly when each sentence starts and ends, but mapping
// individual characters to phonemes would need MeloTTS's word2ph, which its
// inference path discards.
const silence_floor = 0.15;

export function voiced_progress(
  envelope: number[],
  envelope_hz: number,
  span: time_span,
  time: number,
): number {
  const first = Math.max(0, Math.floor(span.start * envelope_hz));
  const last = Math.min(envelope.length, Math.ceil(span.end * envelope_hz));
  const now = Math.min(last, Math.max(first, Math.floor(time * envelope_hz)));

  let elapsed = 0;
  let total = 0;

  for (let index = first; index < last; index += 1) {
    const weight = envelope[index] + silence_floor;
    total += weight;

    if (index < now) {
      elapsed += weight;
    }
  }

  return total > 0 ? Math.min(elapsed / total, 1) : 0;
}

// The inverse of voiced_progress: when has `fraction` of the span's voice been
// spoken. Used to place caption boundaries inside one synthesized piece.
export function time_at_progress(
  envelope: number[],
  envelope_hz: number,
  span: time_span,
  fraction: number,
): number {
  if (fraction <= 0) {
    return span.start;
  }

  if (fraction >= 1) {
    return span.end;
  }

  const first = Math.max(0, Math.floor(span.start * envelope_hz));
  const last = Math.min(envelope.length, Math.ceil(span.end * envelope_hz));

  let total = 0;

  for (let index = first; index < last; index += 1) {
    total += envelope[index] + silence_floor;
  }

  if (total <= 0) {
    return span.start + (span.end - span.start) * fraction;
  }

  const target = total * fraction;
  let running = 0;

  for (let index = first; index < last; index += 1) {
    running += envelope[index] + silence_floor;

    if (running >= target) {
      return Math.min(span.end, Math.max(span.start, (index + 1) / envelope_hz));
    }
  }

  return span.end;
}

// MeloTTS's sentence splitter has a minimum piece length and no maximum, so a
// single synthesized piece can hold two sentences (or one long one). That is
// right for prosody but too coarse to read, so pieces are subdivided for
// display only.
//
// The limit is deliberately short enough that a caption never wraps. The
// karaoke highlight is a right-hand clip, which on a wrapped line would sweep
// both rows at once instead of finishing the first.
const caption_character_limit = 18;

function split_sentences(text: string): string[] {
  const parts = text.match(/[^.!?…]+[.!?…]*\s*/g);

  return (parts ?? [text]).map((part) => part.trim()).filter((part) => part !== "");
}

// Korean writes spaces between 어절, so plain word wrapping works here.
function wrap_words(text: string, limit: number): string[] {
  if (text.length <= limit) {
    return [text];
  }

  const lines: string[] = [];
  let current = "";

  for (const word of text.split(/\s+/)) {
    const candidate = current === "" ? word : `${current} ${word}`;

    if (candidate.length > limit && current !== "") {
      lines.push(current);
      current = word;
    } else {
      current = candidate;
    }
  }

  if (current !== "") {
    lines.push(current);
  }

  return lines;
}

// Subtitle units with their audio ranges. Piece boundaries are exact (the
// server measured them); boundaries introduced inside a piece are placed by
// character share mapped through the loudness curve, which assumes a roughly
// even syllable rate.
export function caption_lines(track: speech_track): speech_segment[] {
  return track.segments.flatMap((segment) => {
    const pieces = split_sentences(segment.text).flatMap((sentence) =>
      wrap_words(sentence, caption_character_limit),
    );

    if (pieces.length <= 1) {
      return [segment];
    }

    const total = pieces.reduce((sum, piece) => sum + piece.length, 0);
    let consumed = 0;

    return pieces.map((piece) => {
      const from = consumed / total;
      consumed += piece.length;

      return {
        start: time_at_progress(track.envelope, track.envelope_hz, segment, from),
        end: time_at_progress(
          track.envelope,
          track.envelope_hz,
          segment,
          consumed / total,
        ),
        text: piece,
      };
    });
  });
}

// The envelope is 50Hz but the face renders at up to 60fps, so neighbouring
// samples are interpolated - stepping straight to the nearest one shows as a
// visible 20ms staircase on the jaw.
export function sample_envelope(
  envelope: number[],
  envelope_hz: number,
  time: number,
): number {
  if (envelope.length === 0) {
    return 0;
  }

  const position = time * envelope_hz;
  const index = Math.floor(position);

  if (index < 0) {
    return envelope[0];
  }

  if (index >= envelope.length - 1) {
    return envelope[envelope.length - 1];
  }

  const fraction = position - index;

  return envelope[index] * (1 - fraction) + envelope[index + 1] * fraction;
}
