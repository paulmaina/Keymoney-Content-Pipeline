import {loadFont} from '@remotion/fonts';
import {Composition, staticFile} from 'remotion';
import {Main} from './Main';
import {FONT} from './theme';
import type {Props} from './types';

// make_video.py copies the --font file into the public dir as font.ttf
loadFont({family: FONT, url: staticFile('font.ttf'), weight: '700'});

const preview: Props = {
  width: 1920,
  height: 1080,
  fps: 30,
  durationInFrames: 150,
  transitionFrames: 16,
  introFrames: 60,
  outroFrames: 106,
  font: 'font.ttf',
  channel: 'Smart Village Boy',
  tagline: "Clever ideas from the world's villages",
  scenes: [],
  captions: [],
  captionStyle: 'clean',
  places: [],
};

export const RemotionRoot: React.FC = () => (
  <Composition
    id="Main"
    component={Main}
    defaultProps={preview}
    durationInFrames={preview.durationInFrames}
    fps={preview.fps}
    width={preview.width}
    height={preview.height}
    calculateMetadata={({props}) => ({
      durationInFrames: props.durationInFrames,
      fps: props.fps,
      width: props.width,
      height: props.height,
    })}
  />
);
