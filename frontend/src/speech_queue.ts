// Serial playback of utterances that arrive asynchronously, on the one <audio>
// element. React-free and DOM-light, in the spirit of speech_track.ts.
//
// Utterances now arrive one at a time, seconds apart, and can arrive with no
// preceding user trigger at all. Each needs its own /tts/speak call, and they
// must play in order without overlapping.

import { fetch_spoken_reply } from "./speech_track";
import type { speech_track, spoken_reply } from "./speech_track";

// Structurally compatible with React's RefObject, declared locally for the same
// reason face_shapes.ts inlines its unions: this module must not depend on the
// component tree.
export type audio_slot = { current: HTMLAudioElement | null };
export type track_slot = { current: speech_track | null };

export type speech_phase = "idle" | "synthesizing" | "speaking" | "blocked";

export type speech_queue_hooks = {
  audio: audio_slot;
  track: track_slot;
  on_phase: (phase: speech_phase) => void;
  on_error: (message: string) => void;
};

export type speech_queue = {
  push: (text: string) => void;
  // Synchronous on purpose: it runs inside the mic tap gesture.
  cancel: () => void;
  // Retry the head item after the browser refused autoplay.
  resume: () => void;
  // Playback is suspended while the mic stream is open - iOS kills the capture
  // track if TTS plays over it. Synthesis is never held, only playback.
  set_hold: (hold: boolean) => void;
};

type end_outcome = "ended" | "failed" | "cancelled";

type queue_item = {
  abort: AbortController;
  synthesis: Promise<spoken_reply>;
  // Whether the synthesis has already landed, so the loop can skip announcing
  // "synthesizing" for it. Without this the phase flips
  // speaking -> synthesizing -> speaking at every utterance boundary, and the
  // face's mood flips talking -> thinking -> talking with it, repainting the
  // brow transition twice per sentence against the rAF loop's frame budget.
  settled: boolean;
};

function is_named_error(error: unknown, name: string): boolean {
  return error instanceof DOMException && error.name === name;
}

export function create_speech_queue(hooks: speech_queue_hooks): speech_queue {
  let items: queue_item[] = [];
  let running = false;
  let blocked = false;
  let held = false;
  // Bumped by cancel(), so a run() that is mid-await knows its work is stale.
  let generation = 0;
  let settle_playback: ((outcome: end_outcome) => void) | null = null;

  const revoke = (spoken: spoken_reply) => URL.revokeObjectURL(spoken.audio_url);

  // Exactly one revoke per created URL. This is the case the old single-slot
  // tts_url_ref could never cover: a synthesis that lands *after* we stopped
  // caring about it still has to be freed.
  const drop = (item: queue_item) => {
    item.abort.abort();
    item.synthesis.then(revoke, () => undefined);
  };

  const wait_for_playback = (audio: HTMLAudioElement): Promise<end_outcome> =>
    new Promise((resolve) => {
      const finish = (outcome: end_outcome) => {
        audio.removeEventListener("ended", on_ended);
        audio.removeEventListener("error", on_failed);
        settle_playback = null;
        resolve(outcome);
      };
      const on_ended = () => finish("ended");
      const on_failed = () => finish("failed");

      settle_playback = finish;
      audio.addEventListener("ended", on_ended);
      audio.addEventListener("error", on_failed);
    });

  const run = async () => {
    if (running || blocked || held) {
      return;
    }

    running = true;

    const my_generation = generation;

    try {
      while (items.length > 0 && !held) {
        const item = items[0];
        const audio = hooks.audio.current;

        if (audio === null) {
          break;
        }

        if (!item.settled) {
          hooks.on_phase("synthesizing");
        }

        let spoken: spoken_reply;

        try {
          // Usually already resolved: synthesis started at push(), so "N+1
          // finished before N" is the normal case rather than a special one.
          spoken = await item.synthesis;
        } catch (error) {
          if (generation !== my_generation) {
            return;
          }

          if (!is_named_error(error, "AbortError")) {
            hooks.on_error(error instanceof Error ? error.message : "합성 실패");
          }

          items = items.slice(1);
          continue;
        }

        if (generation !== my_generation) {
          revoke(spoken);
          return;
        }

        // Re-checked after the await, not just at the loop top: a hold can
        // arrive while this synthesis is in flight, and playing over an open
        // capture session is exactly the iOS conflict hold exists to prevent.
        // The item stays at the head, so unholding replays it from its start.
        if (held) {
          return;
        }

        // Order is load-bearing. pause() first makes the face's `playing`
        // predicate false, so installing the new track cannot be read against the
        // old audio's currentTime - which advance_span_cursor would clamp to the
        // new track's *last* span, flashing the wrong caption at every boundary.
        audio.pause();
        audio.src = spoken.audio_url;
        hooks.track.current = spoken.track;

        // Attached before play() so a clip that ends immediately cannot slip past.
        const playback = wait_for_playback(audio);

        try {
          await audio.play();
        } catch (error) {
          hooks.track.current = null;
          settle_playback?.("failed");

          if (generation !== my_generation) {
            revoke(spoken);
            return;
          }

          if (is_named_error(error, "NotAllowedError")) {
            // Autoplay refused - a scheduled utterance arriving before the page
            // was ever tapped. The item stays at the head for resume().
            blocked = true;
            hooks.on_phase("blocked");
            return;
          }

          // AbortError here means src changed under us, which only cancel() does.
          if (!is_named_error(error, "AbortError")) {
            hooks.on_error(error instanceof Error ? error.message : "재생 실패");
          }

          items = items.slice(1);
          revoke(spoken);
          continue;
        }

        hooks.on_phase("speaking");

        if ((await playback) === "cancelled") {
          // cancel() already dropped and revoked everything.
          return;
        }

        hooks.track.current = null;
        revoke(spoken);
        items = items.slice(1);
      }

      if (!held) {
        hooks.on_phase("idle");
      }
    } finally {
      // One place, so a new bail-out cannot forget it and wedge the queue.
      running = false;
    }
  };

  return {
    push: (text) => {
      const abort = new AbortController();
      // Synthesis starts now, not when this item reaches the head. Piper is
      // ~450ms while utterances arrive seconds apart, so a prefetch-depth
      // scheduler would almost never bind - and the promise itself is the
      // synchronisation primitive, leaving no ordering logic to get wrong.
      const synthesis = fetch_spoken_reply(text, abort.signal);
      const item: queue_item = { abort, synthesis, settled: false };

      // Also the rejection handler: cancel() aborts every pending synthesis at
      // once, and without one attached here those become unhandled rejections.
      // run() does the real handling.
      synthesis.then(
        () => {
          item.settled = true;
        },
        () => undefined,
      );

      items = [...items, item];
      void run();
    },

    cancel: () => {
      generation += 1;

      const dropped = items;
      items = [];
      running = false;
      blocked = false;

      const audio = hooks.audio.current;

      if (audio !== null) {
        audio.pause();
        audio.currentTime = 0;
      }

      hooks.track.current = null;
      settle_playback?.("cancelled");
      // Aborting in flight matters: otherwise a barged-in four-utterance turn
      // leaves four syntheses hogging the single-model TTS service while the
      // user's new turn waits behind them.
      dropped.forEach(drop);
      hooks.on_phase("idle");
    },

    resume: () => {
      blocked = false;
      // The memoised promise returns the same reply and the same object URL, so
      // the retry costs nothing and needs no extra state.
      void run();
    },

    set_hold: (hold) => {
      held = hold;

      // Deliberately does not pause a clip already playing: pause() emits no
      // "ended", so run() would sit forever on wait_for_playback. Holding only
      // stops the *next* utterance from starting, which is all that is needed -
      // handle_mic_click cancels before it opens the mic.
      if (!held) {
        void run();
      }
    },
  };
}
