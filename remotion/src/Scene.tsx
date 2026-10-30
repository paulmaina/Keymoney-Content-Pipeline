import React from 'react';
import {AbsoluteFill, Easing, Img, interpolate, OffthreadVideo, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {BRAND, FONT, outline} from './theme';
import type {Move, Scene as SceneProps} from './types';

// slow in, slow out, never a hard stop mid-scene
const EASE = Easing.bezier(0.45, 0.05, 0.55, 0.95);
// extra push of the foreground layer over the whole scene: subtle, so the duplicated edge never shows
const PARALLAX_SCALE = 0.045;
const PARALLAX_SHIFT = 1.2; // % of the frame, in the direction of the move

const camera = (move: Move, frame: number, frames: number) => {
  const opts = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: EASE} as const;
  const range = [0, Math.max(frames - 1, 1)];
  return {
    scale: interpolate(frame, range, [move.from.scale, move.to.scale], opts),
    x: interpolate(frame, range, [move.from.x, move.to.x], opts),
    y: interpolate(frame, range, [move.from.y, move.to.y], opts),
    progress: interpolate(frame, range, [0, 1], opts),
  };
};

const Picture: React.FC<{scene: SceneProps}> = ({scene}) => {
  const frame = useCurrentFrame();
  const {width, height} = useVideoConfig();
  const cam = camera(scene.move, frame, scene.frames);
  // size the picture to cover the frame ourselves (not object-fit), so the mask lines up with it exactly
  const aspect = (scene.width ?? 1) / (scene.height ?? 1);
  const w = aspect > width / height ? height * aspect : width;
  const h = w / aspect;
  const box: React.CSSProperties = {position: 'absolute', left: (width - w) / 2, top: (height - h) / 2, width: w, height: h};
  const src = staticFile(scene.src!);
  const dx = Math.sign(scene.move.to.x - scene.move.from.x);
  const dy = Math.sign(scene.move.to.y - scene.move.from.y);
  const zoomingOut = scene.move.to.scale < scene.move.from.scale;
  return (
    <AbsoluteFill style={{overflow: 'hidden'}}>
      <div style={{...box, scale: String(cam.scale), translate: `${(cam.x * width) / 100}px ${(cam.y * height) / 100}px`}}>
        <Img src={src} style={{position: 'absolute', inset: 0, width: '100%', height: '100%'}} />
        {scene.mask ? (
          // 2.5D: the near layer grows and drifts a little more than the backdrop, as if the camera moved through it
          <Img
            src={src}
            style={{
              position: 'absolute',
              inset: 0,
              width: '100%',
              height: '100%',
              WebkitMaskImage: `url(${staticFile(scene.mask)})`,
              WebkitMaskSize: '100% 100%',
              maskImage: `url(${staticFile(scene.mask)})`,
              maskSize: '100% 100%',
              transformOrigin: '50% 85%',
              scale: String(1 + PARALLAX_SCALE * (zoomingOut ? 1 - cam.progress : cam.progress)),
              translate: `${dx * PARALLAX_SHIFT * cam.progress}% ${dy * PARALLAX_SHIFT * cam.progress}%`,
            }}
          />
        ) : null}
      </div>
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
  const cam = camera(scene.move, frame, scene.frames);
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
