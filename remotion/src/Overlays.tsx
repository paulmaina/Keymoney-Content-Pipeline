import React, {useMemo} from 'react';
import {AbsoluteFill, useCurrentFrame, useVideoConfig} from 'remotion';
import type {Overlay} from './types';

// Subtle, natural motion over a still picture, drawn in code (remotion_render.py picks up to two per scene from its
// Visuals line and prompt). Everything is seeded per scene, so a frame renders the same every time.

const rand = (seed: number) => {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
};

const wrap = (v: number, size: number) => ((v % size) + size) % size;

type P = {seed: number};

// warm motes catching the light, drifting and twinkling
const Dust: React.FC<P> = ({seed}) => {
  const frame = useCurrentFrame();
  const {width, height, fps} = useVideoConfig();
  const k = Math.min(width, height) / 1080;
  const motes = useMemo(() => {
    const r = rand(seed * 101);
    return Array.from({length: 38}, () => ({x: r() * width, y: r() * height, size: (1.5 + r() * 2.8) * k,
      vx: (6 + r() * 18) * k * (r() < 0.5 ? -1 : 1), vy: (-4 + r() * 6) * k, phase: r() * 6.28, alpha: 0.15 + r() * 0.3}));
  }, [seed, width, height, k]);
  const t = frame / fps;
  return (
    <svg width={width} height={height} style={{position: 'absolute', inset: 0}}>
      {motes.map((m, i) => (
        <circle key={i} cx={wrap(m.x + m.vx * t + Math.sin(t * 0.7 + m.phase) * 6 * k, width)}
          cy={wrap(m.y + m.vy * t + Math.cos(t * 0.5 + m.phase) * 5 * k, height)} r={m.size}
          fill="#FFF1D2" opacity={m.alpha * (0.6 + 0.4 * Math.sin(t * 1.3 + m.phase))} />
      ))}
    </svg>
  );
};

// soft grey puffs rising, swelling and thinning out
const Smoke: React.FC<P> = ({seed}) => {
  const frame = useCurrentFrame();
  const {width, height, fps} = useVideoConfig();
  const puffs = useMemo(() => {
    const r = rand(seed * 211);
    return Array.from({length: 5}, () => ({x: 0.15 + r() * 0.7, period: 8 + r() * 6, offset: r(), size: 0.3 + r() * 0.25}));
  }, [seed]);
  const t = frame / fps;
  return (
    <AbsoluteFill style={{mixBlendMode: 'screen'}}>
      {puffs.map((p, i) => {
        const c = wrap(t / p.period + p.offset, 1);
        const size = width * p.size * (0.6 + c);
        return (
          <div key={i} style={{position: 'absolute', left: width * p.x - size / 2 + Math.sin(c * 6) * width * 0.03,
            top: height * (0.95 - c * 0.8) - size / 2, width: size, height: size, borderRadius: '50%',
            background: 'radial-gradient(circle, rgba(225,225,225,0.22) 0%, rgba(225,225,225,0) 70%)',
            opacity: Math.sin(c * Math.PI) * 0.8, filter: 'blur(12px)'}} />
        );
      })}
    </AbsoluteFill>
  );
};

// wide, blurred bands of mist drifting slowly across
const Mist: React.FC<P> = ({seed}) => {
  const frame = useCurrentFrame();
  const {width, height, fps} = useVideoConfig();
  const bands = useMemo(() => {
    const r = rand(seed * 307);
    return Array.from({length: 3}, (_, i) => ({y: 0.25 + i * 0.22 + r() * 0.08, speed: 0.012 + r() * 0.015,
      offset: r(), alpha: 0.1 + r() * 0.07, dir: r() < 0.5 ? -1 : 1}));
  }, [seed]);
  const t = frame / fps;
  return (
    <AbsoluteFill style={{mixBlendMode: 'screen'}}>
      {bands.map((b, i) => (
        <div key={i} style={{position: 'absolute', left: width * (wrap(b.offset + b.dir * b.speed * t, 1) - 0.8),
          top: height * b.y + Math.sin(t * 0.3 + i) * height * 0.01, width: width * 1.6, height: height * 0.3,
          background: `radial-gradient(ellipse at center, rgba(255,255,255,${b.alpha}) 0%, rgba(255,255,255,0) 70%)`,
          filter: 'blur(28px)'}} />
      ))}
    </AbsoluteFill>
  );
};

// thin, fast, slightly slanted streaks, and a touch of grey in the light
const Rain: React.FC<P> = ({seed}) => {
  const frame = useCurrentFrame();
  const {width, height, fps} = useVideoConfig();
  const k = Math.min(width, height) / 1080;
  const drops = useMemo(() => {
    const r = rand(seed * 401);
    return Array.from({length: 110}, () => ({x: r() * width * 1.2, y: r() * height, len: (0.03 + r() * 0.03) * height,
      speed: (1.5 + r() * 0.9) * height, alpha: 0.16 + r() * 0.2}));
  }, [seed, width, height]);
  const t = frame / fps;
  const slant = 0.18;
  return (
    <AbsoluteFill>
      <AbsoluteFill style={{background: 'rgba(20,30,40,0.12)'}} />
      <svg width={width} height={height} style={{position: 'absolute', inset: 0}}>
        {drops.map((d, i) => {
          const y = wrap(d.y + d.speed * t, height + d.len) - d.len;
          const x = wrap(d.x - y * slant, width * 1.2) - width * 0.1;
          return <line key={i} x1={x} y1={y} x2={x - d.len * slant} y2={y + d.len} stroke="#DCE6F0" strokeWidth={1.2 * k}
            opacity={d.alpha} strokeLinecap="round" />;
        })}
      </svg>
    </AbsoluteFill>
  );
};

// the picture itself shimmers (Scene.tsx applies the filter); this adds the faintest warmth
const Heat: React.FC<P> = () => <AbsoluteFill style={{background: 'rgba(255,190,120,0.05)', mixBlendMode: 'soft-light'}} />;

// warm light from below that breathes and flickers like flames
const Fire: React.FC<P> = ({seed}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = frame / fps + seed;
  const flicker = 0.16 + 0.05 * Math.sin(t * 2 * Math.PI * 1.7) + 0.03 * Math.sin(t * 2 * Math.PI * 4.3 + 1)
    + 0.02 * Math.sin(t * 2 * Math.PI * 9.1 + 2);
  return (
    <AbsoluteFill style={{mixBlendMode: 'screen', opacity: flicker,
      background: 'radial-gradient(ellipse at 50% 100%, rgba(255,140,40,0.9) 0%, rgba(255,100,20,0.35) 40%, rgba(0,0,0,0) 75%)'}} />
  );
};

// a few leaves falling, swaying and turning
const Leaves: React.FC<P> = ({seed}) => {
  const frame = useCurrentFrame();
  const {width, height, fps} = useVideoConfig();
  const k = Math.min(width, height) / 1080;
  const leaves = useMemo(() => {
    const r = rand(seed * 503);
    const colours = ['#7A8F3A', '#A3782F', '#5F7D2A', '#8C6A2B'];
    return Array.from({length: 10}, () => ({x: r() * width, start: r(), speed: 0.07 + r() * 0.06, sway: 0.03 + r() * 0.03,
      size: (11 + r() * 12) * k, spin: (r() - 0.5) * 120, colour: colours[Math.floor(r() * colours.length)], phase: r() * 6.28}));
  }, [seed, width, k]);
  const t = frame / fps;
  return (
    <svg width={width} height={height} style={{position: 'absolute', inset: 0}}>
      {leaves.map((l, i) => {
        const c = wrap(l.start + l.speed * t, 1.15) - 0.1;
        const x = l.x + Math.sin(t * 1.1 + l.phase) * width * l.sway;
        return (
          <g key={i} transform={`translate(${x} ${c * height}) rotate(${l.spin * t + l.phase * 50}) scale(${l.size / 20})`} opacity={0.75}>
            <path d="M0,-10 C6,-6 6,6 0,10 C-6,6 -6,-6 0,-10 Z" fill={l.colour} />
            <path d="M0,-10 L0,10" stroke="rgba(0,0,0,0.25)" strokeWidth={0.8} />
          </g>
        );
      })}
    </svg>
  );
};

// rings spreading and fading on the water in the lower part of the frame
const Ripple: React.FC<P> = ({seed}) => {
  const frame = useCurrentFrame();
  const {width, height, fps} = useVideoConfig();
  const rings = useMemo(() => {
    const r = rand(seed * 601);
    return Array.from({length: 6}, () => ({x: 0.1 + r() * 0.8, y: 0.72 + r() * 0.22, period: 2.6 + r() * 1.6, offset: r()}));
  }, [seed]);
  const t = frame / fps;
  return (
    <svg width={width} height={height} style={{position: 'absolute', inset: 0}}>
      {rings.map((g, i) => {
        const c = wrap(t / g.period + g.offset, 1);
        const rx = width * 0.07 * c;
        return <ellipse key={i} cx={width * g.x} cy={height * g.y} rx={rx} ry={rx * 0.22} fill="none"
          stroke="#FFFFFF" strokeWidth={1.5} opacity={0.28 * (1 - c)} />;
      })}
    </svg>
  );
};

// soft beams of light slanting in from a top corner, slowly breathing
const Rays: React.FC<P> = ({seed}) => {
  const frame = useCurrentFrame();
  const {width, height, fps} = useVideoConfig();
  const beams = useMemo(() => {
    const r = rand(seed * 701);
    return Array.from({length: 4}, (_, i) => ({x: 0.05 + i * 0.13 + r() * 0.05, w: 0.05 + r() * 0.07, phase: r() * 6.28}));
  }, [seed]);
  const left = seed % 2 === 0;
  const t = frame / fps;
  return (
    <AbsoluteFill style={{mixBlendMode: 'screen', transform: left ? undefined : 'scaleX(-1)'}}>
      {beams.map((b, i) => (
        <div key={i} style={{position: 'absolute', left: width * b.x, top: -height * 0.2, width: width * b.w, height: height * 1.5,
          transformOrigin: 'top center', rotate: '-24deg', filter: 'blur(18px)',
          opacity: 0.55 + 0.35 * Math.sin(t * 0.6 + b.phase),
          background: 'linear-gradient(180deg, rgba(255,240,205,0.22) 0%, rgba(255,240,205,0.08) 55%, rgba(255,240,205,0) 100%)'}} />
      ))}
    </AbsoluteFill>
  );
};

const KINDS: Record<Overlay, React.FC<P>> = {dust: Dust, smoke: Smoke, mist: Mist, rain: Rain, heat: Heat, fire: Fire,
  leaves: Leaves, ripple: Ripple, rays: Rays};

export const Overlays: React.FC<{names: Overlay[]; seed: number}> = ({names, seed}) => (
  <AbsoluteFill style={{pointerEvents: 'none'}}>
    {names.map((n, i) => {
      const Kind = KINDS[n];
      return Kind ? <Kind key={n} seed={seed * 7 + i} /> : null;
    })}
  </AbsoluteFill>
);
