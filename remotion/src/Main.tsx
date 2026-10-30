import {linearTiming, TransitionSeries} from '@remotion/transitions';
import {fade} from '@remotion/transitions/fade';
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {Captions} from './Captions';
import {Intro} from './Intro';
import {Outro} from './Outro';
import {Places} from './Places';
import {Scene} from './Scene';
import {BRAND} from './theme';
import type {Props} from './types';

// Scenes overlap by transitionFrames, centred on each narration boundary (remotion_render.py works out the lengths).
export const Main: React.FC<Props> = (props) => {
  const timing = linearTiming({durationInFrames: props.transitionFrames});
  const items: React.ReactNode[] = [];
  const add = (key: string, frames: number, node: React.ReactNode) => {
    if (items.length) {
      items.push(<TransitionSeries.Transition key={`${key}-in`} presentation={fade()} timing={timing} />);
    }
    items.push(
      <TransitionSeries.Sequence key={key} name={key} durationInFrames={frames}>
        {node}
      </TransitionSeries.Sequence>,
    );
  };
  if (props.introFrames) {
    add('Intro', props.introFrames, <Intro channel={props.channel} tagline={props.tagline} part={props.part} />);
  }
  props.scenes.forEach((s, i) => add(`Scene ${i + 1}`, s.frames, <Scene scene={s} />));
  if (props.outroFrames) {
    const next = props.part && props.part.index < props.part.count ? props.part.index + 1 : null;
    add('Outro', props.outroFrames, <Outro channel={props.channel} next={next} />);
  }
  return (
    <AbsoluteFill style={{backgroundColor: BRAND.night}}>
      <TransitionSeries>{items}</TransitionSeries>
      <Places places={props.places} />
      <Captions lines={props.captions} style={props.captionStyle} />
    </AbsoluteFill>
  );
};
