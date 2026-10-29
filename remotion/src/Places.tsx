import {geoCentroid, geoNaturalEarth1, geoPath} from 'd3-geo';
import type {Feature, FeatureCollection, Geometry} from 'geojson';
import React, {useMemo} from 'react';
import {AbsoluteFill, Easing, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {feature} from 'topojson-client';
import type {GeometryCollection, Topology} from 'topojson-specification';
import world from 'world-atlas/countries-110m.json';
import {BRAND, FONT, outline} from './theme';
import type {Place} from './types';

const LABEL_SECONDS = 3.6;
const MAP_SECONDS = 4.2;
const MAP_WIDTH = 0.8; // vertical video: share of the width
const MAP_ASPECT = 0.52;
const MAP_TOP = 0.13; // vertical video: below the Shorts / Reels top bar
const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;

type Country = Feature<Geometry, {name: string}>;
const topo = world as unknown as Topology<{countries: GeometryCollection<{name: string}>}>;
const COUNTRIES = (feature(topo, topo.objects.countries) as FeatureCollection<Geometry, {name: string}>).features as Country[];
const byName = (name: string | null) => COUNTRIES.find((c) => c.properties.name === name);
const pinOf = (p: {country: string | null; lat: number | null; lon: number | null}): [number, number] | null => {
  if (p.lat !== null && p.lon !== null) {
    return [p.lon, p.lat];
  }
  const c = byName(p.country);
  return c ? (geoCentroid(c) as [number, number]) : null;
};

// fades in over `inF` frames from `start`, out over the last `outF` of `length`
const envelope = (t: number, length: number, inF: number, outF: number) =>
  interpolate(t, [0, inF, length - outF, length], [0, 1, 1, 0], clamp);

const Label: React.FC<{place: Place; length: number; underMap: boolean}> = ({place, length, underMap}) => {
  const frame = useCurrentFrame();
  const {width, height, fps} = useVideoConfig();
  const vertical = height > width;
  const top = !vertical ? 76 : underMap ? height * MAP_TOP + width * MAP_WIDTH * MAP_ASPECT + 34 : height * 0.13;
  const t = frame - place.frame;
  const size = vertical ? 54 : 44;
  const bar = interpolate(t, [0, 0.45 * fps], [0, 1], {...clamp, easing: Easing.out(Easing.cubic)});
  const text = interpolate(t, [0.15 * fps, 0.6 * fps], [1, 0], {...clamp, easing: Easing.out(Easing.cubic)});
  return (
    <div
      style={{
        position: 'absolute',
        left: vertical ? 0 : 84,
        right: vertical ? 0 : undefined,
        top,
        display: 'flex',
        justifyContent: vertical ? 'center' : 'flex-start',
        opacity: envelope(t, length, 2, 0.4 * fps),
      }}
    >
      <div style={{display: 'flex', alignItems: 'center', gap: size * 0.35}}>
        <div style={{width: size * 0.16, height: size * 1.25, background: BRAND.yellow, borderRadius: 4, scale: `1 ${bar}`}} />
        <div style={{overflow: 'hidden', paddingRight: 8}}>
          <div
            style={{
              fontFamily: FONT,
              fontWeight: 700,
              fontSize: size,
              color: BRAND.white,
              textShadow: outline(size / 16),
              translate: `0px ${text * size * 1.3}px`,
              whiteSpace: 'nowrap',
            }}
          >
            {place.label}
          </div>
        </div>
      </div>
    </div>
  );
};

const Pin: React.FC<{x: number; y: number; size: number; drop: number; pulse: number; dim?: boolean}> = ({x, y, size, drop, pulse, dim}) => (
  <g transform={`translate(${x}, ${y - (1 - drop) * size * 3})`} opacity={dim ? 0.55 : Math.min(drop * 3, 1)}>
    {pulse > 0 && pulse < 1 ? <circle r={size * (0.4 + pulse * 1.6)} fill="none" stroke={BRAND.yellow} strokeWidth={size * 0.12} opacity={1 - pulse} /> : null}
    <path
      d={`M0,0 C${-size * 0.15},${-size * 0.45} ${-size * 0.55},${-size * 0.6} ${-size * 0.55},${-size}
          A${size * 0.55},${size * 0.55} 0 1 1 ${size * 0.55},${-size}
          C${size * 0.55},${-size * 0.6} ${size * 0.15},${-size * 0.45} 0,0 Z`}
      fill={dim ? BRAND.yellow : BRAND.red}
      stroke="#fff"
      strokeWidth={size * 0.08}
    />
    <circle cy={-size} r={size * 0.2} fill="#fff" />
  </g>
);

const WorldMap: React.FC<{place: Place; length: number}> = ({place, length}) => {
  const frame = useCurrentFrame();
  const {width, height, fps} = useVideoConfig();
  const vertical = height > width;
  const w = vertical ? width * MAP_WIDTH : 560;
  const h = w * MAP_ASPECT;
  const t = frame - place.frame;
  const {projection, paths} = useMemo(() => {
    const proj = geoNaturalEarth1().fitExtent(
      [[8, 8], [w - 8, h - 8]],
      {type: 'Sphere'},
    );
    const path = geoPath(proj);
    return {projection: proj, paths: COUNTRIES.map((c) => ({name: c.properties.name, d: path(c) ?? ''}))};
  }, [w, h]);
  const target = pinOf(place);
  const xy = target ? projection(target) : null;
  if (!xy) {
    return null;
  }
  const visitedXY = place.visited
    .map((name) => pinOf({country: name, lat: null, lon: null}))
    .map((ll) => (ll ? projection(ll) : null))
    .filter((p): p is [number, number] => p !== null);
  const prev = visitedXY[visitedXY.length - 1];
  const zoom = interpolate(t, [0.3 * fps, length], [1, 1.7], {...clamp, easing: Easing.inOut(Easing.sin)});
  const appear = spring({frame: t, fps, config: {damping: 18, stiffness: 140}});
  const drop = spring({frame: t - 0.6 * fps, fps, config: {damping: 11, stiffness: 160}});
  const pulse = interpolate(t, [0.9 * fps, 1.9 * fps], [0, 1], clamp);
  const fill = interpolate(t, [0.5 * fps, 1.1 * fps], [0, 1], clamp);
  const route = interpolate(t, [0.2 * fps, 0.9 * fps], [0, 1], {...clamp, easing: Easing.inOut(Easing.cubic)});
  const pin = 15 / zoom;
  return (
    <div
      style={{
        position: 'absolute',
        right: vertical ? (width - w) / 2 : 70,
        top: vertical ? height * MAP_TOP : 70,
        width: w,
        height: h,
        borderRadius: 22,
        overflow: 'hidden',
        background: 'rgba(14, 42, 59, 0.86)',
        border: '2px solid rgba(255,255,255,0.18)',
        boxShadow: '0 12px 40px rgba(0,0,0,0.45)',
        opacity: envelope(t, length, 0.25 * fps, 0.45 * fps),
        scale: String(interpolate(appear, [0, 1], [0.85, 1])),
        transformOrigin: vertical ? '50% 0%' : '100% 0%',
      }}
    >
      <svg width={w} height={h}>
        <g transform={`translate(${w / 2}, ${h / 2}) scale(${zoom}) translate(${-xy[0]}, ${-xy[1]})`}>
          {paths.map((p) => {
            const isTarget = p.name === place.country;
            const visited = place.visited.includes(p.name);
            return (
              <path
                key={p.name}
                d={p.d}
                fill={isTarget ? BRAND.yellow : visited ? 'rgba(255, 210, 63, 0.45)' : '#2E6A86'}
                fillOpacity={isTarget ? 0.25 + 0.75 * fill : 1}
                stroke="rgba(14, 42, 59, 0.9)"
                strokeWidth={0.6 / zoom}
              />
            );
          })}
          {prev ? (
            <line
              x1={prev[0]}
              y1={prev[1]}
              x2={prev[0] + (xy[0] - prev[0]) * route}
              y2={prev[1] + (xy[1] - prev[1]) * route}
              stroke="#fff"
              strokeWidth={2.2 / zoom}
              strokeDasharray={`${6 / zoom} ${5 / zoom}`}
              opacity={0.8}
            />
          ) : null}
          {visitedXY.map(([x, y], i) => (
            <Pin key={i} x={x} y={y} size={pin * 0.7} drop={1} pulse={0} dim />
          ))}
          <Pin x={xy[0]} y={xy[1]} size={pin} drop={drop} pulse={pulse} />
        </g>
      </svg>
    </div>
  );
};

// A label whenever the narration reaches a new place, and the map the first time each country comes up.
export const Places: React.FC<{places: Place[]}> = ({places}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const shown = places
    .map((p, i) => {
      const next = places[i + 1]?.frame ?? Infinity;
      return {p, label: Math.min(LABEL_SECONDS * fps, next - p.frame), map: Math.min(MAP_SECONDS * fps, next - p.frame)};
    })
    .filter(({p, label, map}) => frame >= p.frame && frame < p.frame + Math.max(label, p.showMap ? map : 0));
  return (
    <AbsoluteFill>
      {shown.map(({p, label, map}) => (
        <React.Fragment key={p.frame}>
          {p.showMap && frame < p.frame + map ? <WorldMap place={p} length={map} /> : null}
          {frame < p.frame + label ? <Label place={p} length={label} underMap={p.showMap && frame < p.frame + map} /> : null}
        </React.Fragment>
      ))}
    </AbsoluteFill>
  );
};
