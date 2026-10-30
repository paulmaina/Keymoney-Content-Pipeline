import React from 'react';
import {Easing, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import {BRAND, FONT, outline} from './theme';
import type {CaptionLine, CaptionStyle, CaptionWord} from './types';

const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;
const POP = 5; // frames for a word (bold) or a line (clean) to arrive
const OUT = 3; // frames to fade a line out

// Sizes in px for 1080-wide vertical / 1920-wide horizontal frames, and how much bigger a key word is.
const LOOK = {
  bold: {vertical: 96, horizontal: 78, key: 1.16, outline: 1 / 8, upper: true},
  clean: {vertical: 64, horizontal: 56, key: 1.06, outline: 1 / 15, upper: false},
  minimal: {vertical: 46, horizontal: 40, key: 1, outline: 1 / 18, upper: false},
} as const;

const BoldWord: React.FC<{w: CaptionWord; frame: number; size: number}> = ({w, frame, size}) => {
  // each word pops in as it's spoken; nothing shows before its time
  const t = frame - w.start;
  const pop = interpolate(t, [0, POP], [0.45, 1], {...clamp, easing: Easing.out(Easing.back(2.2))});
  return (
    <span
      style={{
        display: 'inline-block', // so the pop scales the word without pushing its neighbours
        opacity: t < 0 ? 0 : interpolate(t, [0, 2], [0, 1], clamp),
        scale: String(pop),
        color: w.key ? BRAND.yellow : BRAND.white,
        fontSize: w.key ? size * LOOK.bold.key : size,
        margin: `0 ${size * 0.14}px`,
      }}
    >
      {w.text}
    </span>
  );
};

const CalmWord: React.FC<{w: CaptionWord; frame: number; size: number; style: CaptionStyle; last: boolean}> = ({
  w,
  frame,
  size,
  style,
  last,
}) => {
  const spoken = style === 'clean' && frame >= w.start && frame < Math.max(w.end, w.start + 3);
  const colour = spoken ? BRAND.yellow : w.key && style === 'clean' ? BRAND.sky : BRAND.white;
  return (
    <span style={{color: colour, fontSize: w.key ? size * LOOK[style].key : size}}>
      {w.text}
      {last ? '' : ' '}
    </span>
  );
};

// One caption line at a time. Python cut the lines for the style, flagged key words and chose each line's height (y)
// so it stays off faces and place labels and, in the vertical video, clear of Shorts' title and buttons.
export const Captions: React.FC<{lines: CaptionLine[]; style: CaptionStyle}> = ({lines, style}) => {
  const frame = useCurrentFrame();
  const {width, height} = useVideoConfig();
  const line = lines.find((l) => frame >= l.start && frame < l.end);
  if (!line) {
    return null;
  }
  const vertical = height > width;
  const look = LOOK[style];
  const size = vertical ? look.vertical : look.horizontal;
  const t = frame - line.start;
  const fadeOut = interpolate(frame, [line.end - OUT, line.end], [1, 0], clamp);
  const arrive = style === 'clean' ? interpolate(t, [0, POP], [0, 1], {...clamp, easing: Easing.out(Easing.cubic)}) : 1;
  const words = look.upper ? line.words.map((w) => ({...w, text: w.text.toUpperCase()})) : line.words;
  return (
    <div
      style={{
        position: 'absolute',
        // vertical: keep left of the Shorts / Reels button column on the right
        left: width * (vertical ? 0.06 : 0.1),
        right: width * (vertical ? 0.16 : 0.1),
        top: line.y * height,
        translate: `0 calc(-50% + ${(1 - arrive) * size * 0.25}px)`,
        display: 'flex',
        justifyContent: 'center',
        textAlign: 'center',
        fontFamily: FONT,
        fontWeight: 700,
        lineHeight: 1.18,
        letterSpacing: style === 'bold' ? 0.5 : 0,
        textShadow: outline(size * look.outline),
        opacity: fadeOut * (style === 'bold' ? 1 : arrive),
      }}
    >
      <div style={{flexWrap: 'wrap', display: style === 'bold' ? 'flex' : 'block', justifyContent: 'center', alignItems: 'baseline'}}>
        {words.map((w, i) =>
          style === 'bold' ? (
            <BoldWord key={i} w={w} frame={frame} size={size} />
          ) : (
            <CalmWord key={i} w={w} frame={frame} size={size} style={style} last={i === words.length - 1} />
          ),
        )}
      </div>
    </div>
  );
};
