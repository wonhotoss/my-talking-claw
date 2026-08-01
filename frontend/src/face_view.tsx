// Full-screen animated face. Rendered by app_shell via createElement(face_view)
// - a snake_case component cannot be a JSX tag, since <face_view /> compiles to
// a literal DOM element (and fails under strict TS).
//
// Rendering contract, and the whole reason this is fast enough for a Pi:
//   1. the rAF loop never calls a state setter; it only writes attributes on
//      four cached nodes;
//   2. every JSX attribute on a JS-driven node is a module constant, so React
//      writes it once at mount and can never clobber the loop mid-syllable
//      (app_shell re-renders several times per turn via log_system);
//   3. an unchanged attribute is not written at all, so a resting face costs
//      zero raster work;
//   4. CSS owns everything else that moves, on composited properties only.
// This is the "performance-critical" exception to the declarative rule in
// AGENTS.md, in the same spirit as the lazy model caches in the Python services.

import { useEffect, useRef, useState } from "react";
import type { RefObject } from "react";
import { PanelsTopLeft } from "lucide-react";

import {
  exp_approach,
  mood_shapes,
  mouth_path,
  omega_open,
  spring_step,
  tau_corner,
  tau_round,
  tau_width,
  viseme_shapes,
} from "./face_shapes";
import type { face_mood, mouth_shape, spring_state } from "./face_shapes";
import {
  advance_span_cursor,
  caption_lines,
  sample_envelope,
  voiced_progress,
} from "./speech_track";
import type { speech_segment, speech_track } from "./speech_track";

export type face_view_props = {
  mood: face_mood;
  // What the system is doing right now, shown in the caption area while the
  // face is not speaking.
  status_label: string;
  audio_ref: RefObject<HTMLAudioElement | null>;
  speech_track_ref: RefObject<speech_track | null>;
  // True while the queue still has something to say. Holds the last caption
  // through the src swap between utterances - the track is null for ~100ms
  // there, which would otherwise flash the status label between every pair of
  // sentences.
  speech_pending: boolean;
  on_tap: () => void;
  on_exit: () => void;
};

// Output buffering means the audible position lags audio.currentTime, so the
// face renders slightly in the past. One knob; raise the magnitude for
// Bluetooth output.
const sync_offset_seconds = -0.04;
// Quiet but voiced passages should still open the mouth.
const envelope_gain = 1.35;
// Blinks and expression eases do not need 60fps.
const idle_frame_interval_ms = 33;

const eye_ry_open = 27;
const eye_ry_closed = 2.5;
const blink_ms = 130;
const double_blink_gap_ms = 190;

const rest_mouth_d = mouth_path(mood_shapes.idle);
const rest_brow_transform = "translate(0 0)";
// Evaluated once: the effect below is rebuilt several times per turn, and a
// kiosk's reduced-motion setting does not change under it.
const reduced_motion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function next_blink_gap_ms(mood: face_mood): number {
  const base = 2200 + Math.random() * 3600;
  const scale = mood === "listening" ? 1.6 : mood === "thinking" ? 0.8 : 1.0;

  return base * scale;
}

// Visemes carry the shape, the envelope carries the amplitude. Together they
// hide most of the coarseness of an eight-shape alphabet.
function speaking_shape(
  track: speech_track,
  time: number,
  level: number,
  cursor: { current: number },
): mouth_shape {
  if (track.visemes.length === 0) {
    return flap_shape(level, time);
  }

  cursor.current = advance_span_cursor(track.visemes, cursor.current, time);

  const base = viseme_shapes[track.visemes[cursor.current].viseme];

  return {
    half_width: base.half_width,
    open: base.open * (0.45 + 0.55 * level),
    round: base.round,
    corner: base.corner,
  };
}

// Envelope-only fallback, used when the server sent no timeline. A pure
// open/close piston looks like a nutcracker, so rotate through three vowels
// below syllable rate and let the smoothing crossfade them.
function flap_shape(level: number, time: number): mouth_shape {
  const phase = (time * 0.9) % 1;
  const base =
    phase < 0.34
      ? viseme_shapes.a
      : phase < 0.67
        ? viseme_shapes.e
        : viseme_shapes.o;

  return {
    half_width: base.half_width,
    open: base.open * level,
    round: base.round,
    corner: base.corner,
  };
}

export function face_view(props: face_view_props) {
  const mouth_ref = useRef<SVGPathElement | null>(null);
  const eye_left_ref = useRef<SVGEllipseElement | null>(null);
  const eye_right_ref = useRef<SVGEllipseElement | null>(null);
  const brow_motion_ref = useRef<SVGGElement | null>(null);

  const open_ref = useRef<spring_state>({ value: mood_shapes.idle.open, velocity: 0 });
  const width_ref = useRef(mood_shapes.idle.half_width);
  const round_ref = useRef(mood_shapes.idle.round);
  const corner_ref = useRef(mood_shapes.idle.corner);

  const fill_ref = useRef<HTMLSpanElement | null>(null);

  const cursor_ref = useRef(0);
  const segment_cursor_ref = useRef(0);
  // Absolute performance.now() timestamps, held in a ref so the blink schedule
  // survives the effect rebuild on every mood change - otherwise a busy turn
  // would keep restarting the timer and the face would never blink.
  const blink_ref = useRef({ next_ms: 0, start_ms: -1, doubled: false });
  const last_track_ref = useRef<speech_track | null>(null);
  // Derived from the track once, when the track changes - not every frame.
  const lines_ref = useRef<speech_segment[]>([]);
  const last_mouth_d_ref = useRef(rest_mouth_d);
  const last_eye_ry_ref = useRef(eye_ry_open.toFixed(1));
  const last_brow_ref = useRef(rest_brow_transform);
  const last_clip_ref = useRef("");

  // The spoken sentence changes a handful of times per utterance, which is a
  // discrete event, so it goes through React. The karaoke sweep inside it is
  // continuous and is written straight to the DOM like everything else.
  const [subtitle, set_subtitle] = useState("");
  const last_subtitle_ref = useRef("");

  // The loop is rebuilt when the mood changes (4-5 times a turn, free). The
  // interpolation state lives in refs, so it survives the teardown seamlessly.
  useEffect(() => {
    const mouth = mouth_ref.current;
    const eye_left = eye_left_ref.current;
    const eye_right = eye_right_ref.current;
    const brow_motion = brow_motion_ref.current;

    if (
      mouth === null ||
      eye_left === null ||
      eye_right === null ||
      brow_motion === null
    ) {
      throw new Error("face nodes were not mounted");
    }

    let frame = 0;
    let last_ms = performance.now();

    if (blink_ref.current.next_ms === 0) {
      blink_ref.current.next_ms = last_ms + next_blink_gap_ms(props.mood);
    }

    const step = (now_ms: number) => {
      frame = window.requestAnimationFrame(step);

      const audio = props.audio_ref.current;
      const track = props.speech_track_ref.current;
      const current_time = audio === null ? 0 : audio.currentTime;
      // audio.muted is the unlock_audio() guard: the shared element also plays
      // a silent WAV inside the first tap gesture, and the mouth must not flap
      // for it. currentTime > 0 skips the instant right after play().
      const playing =
        audio !== null &&
        !audio.paused &&
        !audio.ended &&
        !audio.muted &&
        current_time > 0;

      if (!playing && now_ms - last_ms < idle_frame_interval_ms) {
        return;
      }

      const delta = Math.min((now_ms - last_ms) / 1000, 0.05);
      last_ms = now_ms;

      if (track !== last_track_ref.current) {
        last_track_ref.current = track;
        lines_ref.current = track === null ? [] : caption_lines(track);
        cursor_ref.current = 0;
        segment_cursor_ref.current = 0;
      }

      const lip_time = Math.max(current_time + sync_offset_seconds, 0);
      const level =
        playing && track !== null
          ? Math.min(
              sample_envelope(track.envelope, track.envelope_hz, lip_time) *
                envelope_gain,
              1,
            )
          : 0;
      const target =
        playing && track !== null
          ? speaking_shape(track, lip_time, level, cursor_ref)
          : mood_shapes[props.mood];

      spring_step(open_ref.current, target.open, omega_open, delta);
      width_ref.current = exp_approach(
        width_ref.current,
        target.half_width,
        tau_width,
        delta,
      );
      round_ref.current = exp_approach(round_ref.current, target.round, tau_round, delta);
      corner_ref.current = exp_approach(
        corner_ref.current,
        target.corner,
        tau_corner,
        delta,
      );

      const next_mouth_d = mouth_path({
        half_width: width_ref.current,
        open: Math.max(open_ref.current.value, 0),
        round: round_ref.current,
        corner: corner_ref.current,
      });

      // Coordinates are quantised to 0.1 units, so once the smoothing settles
      // the string stops changing and DOM writes drop to zero.
      if (next_mouth_d !== last_mouth_d_ref.current) {
        mouth.setAttribute("d", next_mouth_d);
        last_mouth_d_ref.current = next_mouth_d;
      }

      const lines = lines_ref.current;
      const captioned = playing && track !== null && lines.length > 0;

      if (captioned) {
        segment_cursor_ref.current = advance_span_cursor(
          lines,
          segment_cursor_ref.current,
          current_time,
        );
      }

      const spoken = captioned ? lines[segment_cursor_ref.current] : null;

      if (spoken !== null && track !== null) {
        // Karaoke sweep: the fill layer is clipped from the right, so the
        // highlight grows left-to-right over the base text.
        const swept = voiced_progress(
          track.envelope,
          track.envelope_hz,
          spoken,
          current_time,
        );
        const next_clip = `inset(0 ${((1 - swept) * 100).toFixed(1)}% 0 0)`;

        if (next_clip !== last_clip_ref.current && fill_ref.current !== null) {
          fill_ref.current.style.clipPath = next_clip;
          last_clip_ref.current = next_clip;
        }
      }

      const next_subtitle =
        spoken === null
          ? props.speech_pending
            ? last_subtitle_ref.current
            : ""
          : spoken.text;

      if (next_subtitle !== last_subtitle_ref.current) {
        last_subtitle_ref.current = next_subtitle;
        last_clip_ref.current = "";
        set_subtitle(next_subtitle);
      }

      if (reduced_motion) {
        return;
      }

      const blink = blink_ref.current;

      if (blink.start_ms < 0 && now_ms >= blink.next_ms) {
        blink.start_ms = now_ms;
      }

      const blink_elapsed = blink.start_ms < 0 ? blink_ms : now_ms - blink.start_ms;

      if (blink.start_ms >= 0 && blink_elapsed >= blink_ms) {
        // A doubled blink never chains into a third.
        blink.doubled = !blink.doubled && Math.random() < 0.15;
        blink.next_ms =
          now_ms +
          (blink.doubled ? double_blink_gap_ms : next_blink_gap_ms(props.mood));
        blink.start_ms = -1;
      }

      const openness =
        blink_elapsed >= blink_ms
          ? 1
          : 1 - Math.sin(Math.PI * (blink_elapsed / blink_ms));
      const eye_ry = (
        eye_ry_closed +
        (eye_ry_open - eye_ry_closed) * openness
      ).toFixed(1);

      if (eye_ry !== last_eye_ry_ref.current) {
        eye_left.setAttribute("ry", eye_ry);
        eye_right.setAttribute("ry", eye_ry);
        last_eye_ry_ref.current = eye_ry;
      }

      const next_brow = `translate(0 ${(-5 * level).toFixed(1)})`;

      if (next_brow !== last_brow_ref.current) {
        brow_motion.setAttribute("transform", next_brow);
        last_brow_ref.current = next_brow;
      }
    };

    frame = window.requestAnimationFrame(step);

    return () => window.cancelAnimationFrame(frame);
    // speech_pending is read inside the loop, so it has to be a dependency: the
    // rAF closure is long-lived and a non-dependency prop would stay frozen at
    // mount, making the flicker look intermittent rather than fixed. Rebuilding
    // is free - it already happens on every mood change, and all interpolation
    // state lives in refs.
  }, [props.mood, props.speech_pending, props.audio_ref, props.speech_track_ref]);

  return (
    <div className="face_stage_wrap">
      <button
        className="face_stage"
        type="button"
        onClick={props.on_tap}
        aria-label="말하기"
      >
        <svg
          className={`face_svg face_${props.mood}`}
          viewBox="0 0 400 400"
          preserveAspectRatio="xMidYMid meet"
          aria-hidden="true"
        >
          <circle className="face_ring" cx={200} cy={200} r={178} />
          <circle className="face_cheek" cx={104} cy={232} r={22} />
          <circle className="face_cheek" cx={296} cy={232} r={22} />
          {/* Two nested groups: CSS owns the outer transform (mood), JS owns
              the inner one. A CSS transform on a node silently defeats the SVG
              transform attribute, so they must never be the same element. */}
          <g className="face_brows">
            <g ref={brow_motion_ref} transform={rest_brow_transform}>
              <path className="face_ink face_brow_left" d="M 112 116 L 164 110" />
              <path className="face_ink face_brow_right" d="M 236 110 L 288 116" />
            </g>
          </g>
          {/* ry is JS-owned for blinking, so "wide eyes" while listening comes
              from a CSS scale on this wrapper, never from ry. */}
          <g className="face_eyes">
            <ellipse
              ref={eye_left_ref}
              className="face_ink"
              cx={138}
              cy={168}
              rx={23}
              ry={eye_ry_open}
            />
            <ellipse
              ref={eye_right_ref}
              className="face_ink"
              cx={262}
              cy={168}
              rx={23}
              ry={eye_ry_open}
            />
          </g>
          <path ref={mouth_ref} className="face_ink face_mouth" d={rest_mouth_d} />
        </svg>
      </button>

      {/* pointer-events: none in CSS, so taps fall through to the face button. */}
      <div className="face_caption" aria-live="polite">
        {subtitle === "" ? (
          <p className="face_status">{props.status_label}</p>
        ) : (
          <p className="face_subtitle">
            <span className="face_subtitle_base">{subtitle}</span>
            {/* Same text on top, clipped from the right by the rAF loop. */}
            <span className="face_subtitle_fill" ref={fill_ref} aria-hidden="true">
              {subtitle}
            </span>
          </p>
        )}
      </div>

      {/* Sibling of the face button, not a child: interactive elements cannot
          nest, and hit-testing on a nested one is undefined. */}
      <button
        className="face_exit"
        type="button"
        onClick={props.on_exit}
        aria-label="콘솔로 돌아가기"
      >
        <PanelsTopLeft size={18} />
        <span>콘솔</span>
      </button>
    </div>
  );
}
