import React from 'react';
import {AbsoluteFill, Easing, Img, interpolate, OffthreadVideo, Sequence, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {Overlays} from './Overlays';
import {BRAND, FONT, outline} from './theme';
import type {Move, Overlay, Parallax, Scene as SceneProps, Shot} from './types';

// slow in, slow out, never a hard stop mid-scene (remotion_render.py may send a bolder curve per scene)
const CALM = [0.45, 0.05, 0.55, 0.95];
const DISSOLVE = 4; // frames: the cut between a scene's two shots is softened just enough not to jolt
const PLATE_MARGIN = 0.03; // the filled-in backdrop is drawn this much larger, so its own drift never shows an edge

const camera = (move: Move, frame: number, frames: number, ease: number[]) => {
  const [a, b, c, d] = ease;
  const opts = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: Easing.bezier(a, b, c, d)} as const;
  const range = [0, Math.max(frames - 1, 1)];
  return {
    scale: interpolate(frame, range, [move.from.scale, move.to.scale], opts),
    x: interpolate(frame, range, [move.from.x, move.to.x], opts),
    y: interpolate(frame, range, [move.from.y, move.to.y], opts),
    progress: interpolate(frame, range, [0, 1], opts),
  };
};

// heat shimmer: rising noise displaces the picture by a few pixels
const HeatFilter: React.FC<{id: string}> = ({id}) => {
  const frame = useCurrentFrame();
  return (
    <svg width={0} height={0} style={{position: 'absolute'}}>
      <filter id={id} x="0" y="0" width="100%" height="100%">
        <feTurbulence type="fractalNoise" baseFrequency="0.006 0.045" numOctaves={2} seed={3} result="noise" />
        <feOffset in="noise" dy={-frame * 0.9} result="rising" />
        <feDisplacementMap in="SourceGraphic" in2="rising" scale={5} xChannelSelector="R" yChannelSelector="G" />
      </filter>
    </svg>
  );
};

const ShotView: React.FC<{shot: Shot; frames: number; ease: number[]; parallax?: Parallax | null; heat?: string}> = ({
  shot,
  frames,
  ease,
  parallax,
  heat,
}) => {
  const frame = useCurrentFrame();
  const {width, height} = useVideoConfig();
  const cam = camera(shot.move, frame, frames, ease);
  // size the picture to cover the frame ourselves (not object-fit), so the mask lines up with it exactly
  const aspect = shot.width / shot.height;
  const w = aspect > width / height ? height * aspect : width;
  const h = w / aspect;
  const box: React.CSSProperties = {position: 'absolute', left: (width - w) / 2, top: (height - h) / 2, width: w, height: h};
  const src = staticFile(shot.src);
  const dx = Math.sign(shot.move.to.x - shot.move.from.x) || 1;
  const dy = Math.sign(shot.move.to.y - shot.move.from.y);
  const zoomingOut = shot.move.to.scale < shot.move.from.scale;
  const depth = shot.mask && parallax ? parallax : null;
  const grow = depth ? depth.scale * (zoomingOut ? 1 - cam.progress : cam.progress) : 0;
  const fill: React.CSSProperties = {position: 'absolute', inset: 0, width: '100%', height: '100%'};
  return (
    <AbsoluteFill style={{overflow: 'hidden'}}>
      <div
        style={{
          ...box,
          scale: String(cam.scale),
          translate: `${(cam.x * width) / 100}px ${(cam.y * height) / 100}px`,
          filter: heat ? `url(#${heat})` : undefined,
        }}
      >
        {depth && shot.plate ? (
          // the backdrop with the subject filled in drifts slightly against the move: what the subject uncovers is
          // background, never a stretched copy of its own edge
          <Img
            src={staticFile(shot.plate)}
            style={{
              ...fill,
              scale: String(1 + PLATE_MARGIN),
              translate: `${-dx * depth.back * cam.progress}% ${-dy * depth.back * cam.progress}%`,
            }}
          />
        ) : (
          <Img src={src} style={fill} />
        )}
        {depth ? (
          // 2.5D: the near layer grows and drifts more than the backdrop, as if the camera moved through the scene
          <Img
            src={src}
            style={{
              ...fill,
              WebkitMaskImage: `url(${staticFile(shot.mask!)})`,
              WebkitMaskSize: '100% 100%',
              maskImage: `url(${staticFile(shot.mask!)})`,
              maskSize: '100% 100%',
              transformOrigin: '50% 85%',
              scale: String(1 + grow),
              translate: `${dx * depth.shift * cam.progress}% ${dy * depth.shift * cam.progress}%`,
            }}
          />
        ) : null}
      </div>
    </AbsoluteFill>
  );
};

const Picture: React.FC<{scene: SceneProps}> = ({scene}) => {
  const frame = useCurrentFrame();
  const shots: Shot[] = scene.shots?.length
    ? scene.shots
    : [{src: scene.src!, width: scene.width ?? 1, height: scene.height ?? 1, mask: scene.mask, move: scene.move, from: 0}];
  const ease = scene.ease ?? CALM;
  const overlays: Overlay[] = scene.overlays ?? [];
  const heat = overlays.includes('heat') ? `heat-${scene.seed ?? 0}` : undefined;
  return (
    <AbsoluteFill>
      {heat ? <HeatFilter id={heat} /> : null}
      {shots.map((shot, j) => {
        const start = j === 0 ? 0 : shot.from - DISSOLVE / 2;
        const end = j + 1 < shots.length ? shots[j + 1].from + DISSOLVE / 2 : scene.frames;
        const opacity = j === 0 ? 1 : interpolate(frame - start, [0, DISSOLVE], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
        return (
          <Sequence key={j} from={start} durationInFrames={Math.max(end - start, 1)} layout="none">
            <AbsoluteFill style={{opacity}}>
              <ShotView shot={shot} frames={end - start} ease={ease} parallax={scene.parallax} heat={heat} />
            </AbsoluteFill>
          </Sequence>
        );
      })}
      {overlays.length ? <Overlays names={overlays} seed={scene.seed ?? 1} /> : null}
    </AbsoluteFill>
  );
};

// stock footage moves by itself; Python has already cropped it to the frame and cut it to the scene's length
const Clip: React.FC<{scene: SceneProps}> = ({scene}) => (
  <AbsoluteFill>
    <OffthreadVideo src={staticFile(scene.src!)} muted style={{width: '100%', height: '100%', objectFit: 'cover'}} />
  </AbsoluteFill>
);

const Card: React.FC<{scene: SceneProps}> = ({scene}) => {
  const frame = useCurrentFrame();
  const {width, height} = useVideoConfig();
  const cam = camera(scene.move, frame, scene.frames, scene.ease ?? CALM);
  const size = Math.min(width, height) / 14;
  return (
    <AbsoluteFill
      style={{
        background: `radial-gradient(circle at 50% 40%, ${BRAND.nightLight}, ${BRAND.night})`,
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <div
        style={{
          scale: String(cam.scale),
          width: '80%',
          textAlign: 'center',
          fontFamily: FONT,
          fontWeight: 700,
          fontSize: size,
          lineHeight: 1.3,
          color: BRAND.white,
          textShadow: outline(size / 18),
        }}
      >
        {scene.text}
      </div>
    </AbsoluteFill>
  );
};

export const Scene: React.FC<{scene: SceneProps}> = ({scene}) =>
  scene.kind === 'video' ? <Clip scene={scene} /> : scene.kind === 'card' ? <Card scene={scene} /> : <Picture scene={scene} />;
