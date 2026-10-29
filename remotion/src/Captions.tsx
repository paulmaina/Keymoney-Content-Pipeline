import React from 'react';
import {AbsoluteFill, Easing, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import {BRAND, FONT, outline} from './theme';
import type {CaptionLine} from './types';

const POP = 5; // frames for a line to pop in

// One caption line at a time (the same lines as the .srt). The line pops up, and each word turns yellow while it's spoken.
export const Captions: React.FC<{lines: CaptionLine[]}> = ({lines}) => {
  const frame = useCurrentFrame();
  const {width, height} = useVideoConfig();
  const line = lines.find((l) => frame >= l.start && frame < l.end);
  if (!line) {
    return null;
  }
  const vertical = height > width;
  const size = vertical ? 74 : 62;
  const t = frame - line.start;
  const ease = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.out(Easing.back(1.6))} as const;
  return (
    <AbsoluteFill
      style={{
        // vertical: above the Shorts / Reels buttons and description; horizontal: the usual lower third
        justifyContent: vertical ? 'flex-start' : 'flex-end',
        paddingTop: vertical ? height * 0.66 : 0,
        paddingBottom: vertical ? 0 : height * 0.075,
        alignItems: 'center',
      }}
    >
      <div
        style={{
          maxWidth: width * (vertical ? 0.86 : 0.8),
          textAlign: 'center',
          fontFamily: FONT,
          fontWeight: 700,
          fontSize: size,
          lineHeight: 1.25,
          color: BRAND.white,
          textShadow: outline(size / 14),
          opacity: interpolate(t, [0, POP - 2], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}),
          scale: String(interpolate(t, [0, POP], [0.86, 1], ease)),
          translate: `0px ${interpolate(t, [0, POP], [size * 0.3, 0], ease)}px`,
        }}
      >
        {line.words.map((w, i) => {
          const active = frame >= w.start && frame < Math.max(w.end, w.start + 3);
          return (
            // colour only: scaling the spoken word would push it into its neighbours
            <span key={i} style={{color: active ? BRAND.yellow : BRAND.white}}>
              {w.text}
              {i < line.words.length - 1 ? ' ' : ''}
            </span>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
