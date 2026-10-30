import React from 'react';
import {AbsoluteFill, Easing, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {BRAND, FONT, outline} from './theme';
import type {Part} from './types';

const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;

// A rising sun over a hill, then the channel name word by word and the tagline. About 2 seconds.
// In a Shorts series, a "PART 1 OF 4" badge pops in first, above the name.
export const Intro: React.FC<{channel: string; tagline: string; part?: Part | null}> = ({channel, tagline, part}) => {
  const frame = useCurrentFrame();
  const {width, height, fps} = useVideoConfig();
  const unit = Math.min(width, height);
  const sun = spring({frame, fps, config: {damping: 14, stiffness: 120}});
  const words = channel.toUpperCase().split(' ');
  const size = unit * (height > width ? 0.11 : 0.1);
  return (
    <AbsoluteFill style={{background: `linear-gradient(180deg, ${BRAND.nightLight} 0%, ${BRAND.night} 70%)`, alignItems: 'center', justifyContent: 'center'}}>
      <svg width={width} height={height} style={{position: 'absolute', inset: 0}}>
        <defs>
          <radialGradient id="glow">
            <stop offset="0%" stopColor={BRAND.yellow} stopOpacity={0.55} />
            <stop offset="100%" stopColor={BRAND.yellow} stopOpacity={0} />
          </radialGradient>
        </defs>
        <circle cx={width / 2} cy={height * 0.44 + (1 - sun) * unit * 0.35} r={unit * 0.42 * sun} fill="url(#glow)" />
        <circle cx={width / 2} cy={height * 0.44 + (1 - sun) * unit * 0.35} r={unit * 0.17} fill={BRAND.yellow} />
        <path
          d={`M0,${height * 0.6} Q${width * 0.25},${height * 0.52} ${width * 0.5},${height * 0.6} T${width},${height * 0.57} V${height} H0 Z`}
          fill="#163E54"
        />
        <path
          d={`M0,${height * 0.7} Q${width * 0.35},${height * 0.62} ${width * 0.7},${height * 0.7} T${width},${height * 0.68} V${height} H0 Z`}
          fill={BRAND.night}
        />
      </svg>
      <div style={{display: 'flex', gap: size * 0.3, flexWrap: 'wrap', justifyContent: 'center', width: '90%', zIndex: 1}}>
        {words.map((word, i) => {
          const s = spring({frame: frame - 6 - i * 4, fps, config: {damping: 13, stiffness: 150}});
          return (
            <div
              key={i}
              style={{
                fontFamily: FONT,
                fontWeight: 700,
                fontSize: size,
                color: i === words.length - 1 ? BRAND.yellow : BRAND.white,
                textShadow: outline(size / 22),
                opacity: Math.min(s * 1.5, 1),
                translate: `0px ${(1 - s) * size * 0.8}px`,
                scale: String(0.8 + 0.2 * s),
              }}
            >
              {word}
            </div>
          );
        })}
      </div>
      <div
        style={{
          position: 'absolute',
          top: height * 0.5 + size * (height > width ? 1.6 : 0.95),
          fontFamily: FONT,
          fontWeight: 700,
          fontSize: size * 0.32,
          color: BRAND.white,
          letterSpacing: '0.04em',
          textShadow: outline(2),
          opacity: interpolate(frame, [0.75 * fps, 1.15 * fps], [0, 0.95], {...clamp, easing: Easing.out(Easing.quad)}),
          textAlign: 'center',
          width: '90%',
        }}
      >
        {tagline}
      </div>
      {part ? <PartBadge part={part} top={height * 0.5 - size * (height > width ? 2.3 : 1.7)} size={size * 0.42} /> : null}
    </AbsoluteFill>
  );
};

const PartBadge: React.FC<{part: Part; top: number; size: number}> = ({part, top, size}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const pop = spring({frame, fps, config: {damping: 12, stiffness: 170}});
  return (
    <div
      style={{
        position: 'absolute',
        top,
        padding: `${size * 0.3}px ${size * 0.8}px`,
        borderRadius: size,
        background: BRAND.red,
        fontFamily: FONT,
        fontWeight: 700,
        fontSize: size,
        color: BRAND.white,
        letterSpacing: '0.06em',
        boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
        scale: String(pop),
      }}
    >
      PART {part.index} OF {part.count}
    </div>
  );
};
