import React from 'react';
import {AbsoluteFill, Easing, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {BRAND, FONT, outline} from './theme';

const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;

// "Thanks for watching", then a Subscribe button that a cursor clicks. About 3.5 seconds.
export const Outro: React.FC<{channel: string}> = ({channel}) => {
  const frame = useCurrentFrame();
  const {width, height, fps} = useVideoConfig();
  const unit = Math.min(width, height);
  const title = spring({frame: frame - 4, fps, config: {damping: 14}});
  const button = spring({frame: frame - 0.5 * fps, fps, config: {damping: 10, stiffness: 140}});
  const clickAt = 1.7 * fps;
  const cursor = interpolate(frame, [0.9 * fps, clickAt], [0, 1], {...clamp, easing: Easing.inOut(Easing.cubic)});
  const pressed = frame >= clickAt && frame < clickAt + 4;
  const subscribed = frame >= clickAt + 2;
  const size = unit * 0.075;
  const bw = size * 5.2;
  const bh = size * 1.45;
  return (
    <AbsoluteFill style={{background: `radial-gradient(circle at 50% 35%, ${BRAND.nightLight}, ${BRAND.night})`, alignItems: 'center', justifyContent: 'center'}}>
      <div
        style={{
          position: 'absolute',
          top: height * 0.5 - size * 3.2,
          fontFamily: FONT,
          fontWeight: 700,
          fontSize: size,
          color: BRAND.white,
          textShadow: outline(size / 20),
          opacity: title,
          translate: `0px ${(1 - title) * size}px`,
        }}
      >
        Thanks for watching
      </div>
      <div
        style={{
          width: bw,
          height: bh,
          borderRadius: bh / 2,
          background: subscribed ? '#555' : BRAND.red,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontFamily: FONT,
          fontWeight: 700,
          fontSize: size * 0.62,
          color: BRAND.white,
          letterSpacing: '0.05em',
          scale: String(button * (pressed ? 0.93 : 1)),
          boxShadow: '0 10px 30px rgba(0,0,0,0.4)',
        }}
      >
        {subscribed ? 'SUBSCRIBED ✓' : 'SUBSCRIBE'}
      </div>
      <div
        style={{
          position: 'absolute',
          top: height * 0.5 + bh * 0.8,
          fontFamily: FONT,
          fontWeight: 700,
          fontSize: size * 0.55,
          color: BRAND.yellow,
          textShadow: outline(2),
          opacity: interpolate(frame, [0.8 * fps, 1.2 * fps], [0, 1], clamp),
        }}
      >
        {channel}
      </div>
      {/* a simple arrow cursor gliding onto the button */}
      <svg
        width={size}
        height={size}
        viewBox="0 0 24 24"
        style={{
          position: 'absolute',
          left: width / 2 + interpolate(cursor, [0, 1], [bw * 1.2, bw * 0.15]),
          top: height / 2 + interpolate(cursor, [0, 1], [bh * 2.4, bh * 0.05]),
          opacity: interpolate(frame, [0.8 * fps, 1 * fps, 2.6 * fps, 2.9 * fps], [0, 1, 1, 0], clamp),
          scale: pressed ? '0.85' : '1',
        }}
      >
        <path d="M3 2 L3 20 L8 15 L11.5 22 L14.5 20.6 L11 13.8 L18 13.8 Z" fill="#fff" stroke="#000" strokeWidth="1.4" />
      </svg>
    </AbsoluteFill>
  );
};
