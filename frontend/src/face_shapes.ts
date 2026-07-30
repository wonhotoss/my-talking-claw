// Mouth geometry, mood selection and the smoothing math for the face view.
// Pure: no React, no DOM. Numbers are in the face's 0..400 viewBox units.

import type { viseme_kind } from "./speech_track";

export type mouth_shape = {
  half_width: number;
  open: number;
  round: number;
  corner: number;
};

export type face_mood = "denied" | "talking" | "listening" | "thinking" | "idle";

export type spring_state = {
  value: number;
  velocity: number;
};

export const mouth_center_x = 200;
export const mouth_center_y = 268;

// Fuller than the circle constant (0.5523) so the lips read as plump rather
// than as a stretched ellipse.
const mouth_kappa = 0.62;

export const viseme_shapes: Record<viseme_kind, mouth_shape> = {
  a: { half_width: 42, open: 62, round: 0.05, corner: 0.0 },
  e: { half_width: 48, open: 30, round: 0.0, corner: 0.22 },
  i: { half_width: 54, open: 14, round: 0.0, corner: 0.4 },
  o: { half_width: 30, open: 46, round: 0.78, corner: 0.0 },
  u: { half_width: 24, open: 24, round: 1.0, corner: -0.06 },
  m: { half_width: 38, open: 0, round: 0.1, corner: 0.1 },
  n: { half_width: 40, open: 12, round: 0.05, corner: 0.1 },
  x: { half_width: 36, open: 4, round: 0.0, corner: 0.14 },
};

export const mood_shapes: Record<face_mood, mouth_shape> = {
  idle: { half_width: 36, open: 4, round: 0.0, corner: 0.18 },
  listening: { half_width: 26, open: 16, round: 0.62, corner: 0.05 },
  thinking: { half_width: 28, open: 3, round: 0.1, corner: -0.1 },
  talking: { half_width: 36, open: 4, round: 0.0, corner: 0.1 },
  denied: { half_width: 34, open: 3, round: 0.0, corner: -0.34 },
};

// Time constants, never per-frame alphas: a Pi that drops to 30fps must
// produce the same motion as a desktop at 60.
export const omega_open = 42; // rad/s, critically damped jaw
export const tau_width = 0.06;
export const tau_round = 0.07;
export const tau_corner = 0.13; // expression-level, deliberately slow

// A cubic whose controls sit `o` from the endpoints peaks at 0.75*o, so the
// coefficients below are pre-divided by 0.75 and `open` is a true aperture.
// At open === 0 the path degenerates to a zero-area lens, which with a round
// stroke linecap renders as exactly the closed line we want for "m".
export function mouth_path(shape: mouth_shape): string {
  const width = shape.half_width * (1 - 0.22 * shape.round);
  const upper = shape.open * (0.56 + 0.3 * shape.round);
  const lower = shape.open * (0.78 + 0.14 * shape.round);
  const lift = shape.corner * 9;
  const control = width * mouth_kappa;

  const left_x = (mouth_center_x - width).toFixed(1);
  const right_x = (mouth_center_x + width).toFixed(1);
  const edge_y = (mouth_center_y - lift).toFixed(1);
  const control_left = (mouth_center_x - control).toFixed(1);
  const control_right = (mouth_center_x + control).toFixed(1);
  const top_y = (mouth_center_y - upper).toFixed(1);
  const bottom_y = (mouth_center_y + lower).toFixed(1);

  return (
    `M ${left_x} ${edge_y} ` +
    `C ${control_left} ${top_y} ${control_right} ${top_y} ${right_x} ${edge_y} ` +
    `C ${control_right} ${bottom_y} ${control_left} ${bottom_y} ${left_x} ${edge_y} Z`
  );
}

export function exp_approach(
  current: number,
  target: number,
  tau: number,
  delta: number,
): number {
  return target + (current - target) * Math.exp(-delta / tau);
}

// Closed-form critically damped step. Explicit Euler diverges once
// omega * delta exceeds ~1, which a 200ms stall on a Pi would reach; this form
// is unconditionally stable. Mutates in place because it runs 60x/sec and a
// fresh object per frame is pure garbage - the performance-critical exception.
export function spring_step(
  state: spring_state,
  target: number,
  omega: number,
  delta: number,
): void {
  const offset = state.value - target;
  const decay = Math.exp(-omega * delta);
  const coefficient = state.velocity + omega * offset;

  state.value = target + (offset + coefficient * delta) * decay;
  state.velocity = (state.velocity - coefficient * omega * delta) * decay;
}

// The parameter types are inlined string unions rather than imported from
// app.tsx: structural typing accepts the app's aliases at the call site, and
// importing would make app -> face_view -> face_shapes -> app a cycle.
export function pick_mood(
  permission: "pending" | "granted" | "denied",
  activity:
    | "waiting"
    | "recording"
    | "transcribing"
    | "thinking"
    | "synthesizing"
    | "speaking",
): face_mood {
  return permission === "denied"
    ? "denied"
    : activity === "speaking"
      ? "talking"
      : activity === "recording"
        ? "listening"
        : activity === "waiting"
          ? "idle"
          : "thinking";
}
