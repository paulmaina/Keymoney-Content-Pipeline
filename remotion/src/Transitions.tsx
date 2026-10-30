import type {TransitionPresentation, TransitionPresentationComponentProps} from '@remotion/transitions';
import React from 'react';
import {AbsoluteFill, Easing, interpolate} from 'remotion';

// Chapter transitions for --camera bold (remotion_render.py picks them; every other cut is a crossfade).
// They use the same number of frames as the crossfade, so the timeline maths doesn't change.

type Empty = Record<string, never>;
const ease = Easing.inOut(Easing.cubic);

// a fast sideways whip: both scenes slide with motion blur that peaks mid-move
const Whip: React.FC<TransitionPresentationComponentProps<Empty>> = ({children, presentationDirection, presentationProgress}) => {
  const p = ease(presentationProgress);
  const entering = presentationDirection === 'entering';
  const x = entering ? (1 - p) * 100 : -p * 100;
  const blur = Math.sin(presentationProgress * Math.PI) * 28;
  return <AbsoluteFill style={{translate: `${x}% 0`, filter: `blur(${blur}px)`}}>{children}</AbsoluteFill>;
};

// a zoom through: the old scene rushes forward and fades, the new one settles in from slightly smaller
const Zoom: React.FC<TransitionPresentationComponentProps<Empty>> = ({children, presentationDirection, presentationProgress}) => {
  const p = ease(presentationProgress);
  const entering = presentationDirection === 'entering';
  const scale = entering ? interpolate(p, [0, 1], [0.85, 1]) : interpolate(p, [0, 1], [1, 1.6]);
  const opacity = entering ? interpolate(p, [0, 0.6], [0, 1], {extrapolateRight: 'clamp'}) : interpolate(p, [0.4, 1], [1, 0], {extrapolateLeft: 'clamp'});
  const blur = Math.sin(presentationProgress * Math.PI) * 10;
  return <AbsoluteFill style={{scale: String(scale), opacity, filter: `blur(${blur}px)`}}>{children}</AbsoluteFill>;
};

export const whip = (): TransitionPresentation<Empty> => ({component: Whip, props: {} as Empty});
export const zoomThrough = (): TransitionPresentation<Empty> => ({component: Zoom, props: {} as Empty});
