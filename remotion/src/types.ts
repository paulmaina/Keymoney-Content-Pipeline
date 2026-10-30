// Props written by remotion_render.py. All times are in frames on the final timeline.

export type Move = {
  // start and end of the camera: zoom (1 = image just covers the frame) and offset as a % of the frame
  from: {scale: number; x: number; y: number};
  to: {scale: number; x: number; y: number};
  name: string;
};

export type Scene = {
  frames: number; // sequence length, including the transition overlap on each side
  kind: 'image' | 'video' | 'card';
  src?: string; // image, or a stock clip already cut to the scene's length
  width?: number; // image size, so the mask lines up with the cover-cropped picture
  height?: number;
  mask?: string; // foreground mask for the 2.5D parallax
  text?: string; // title card text
  move: Move;
};

export type CaptionWord = {text: string; start: number; end: number; key: boolean}; // key: number, place, name or hack name
export type CaptionLine = {start: number; end: number; y: number; words: CaptionWord[]}; // y: centre, share of the height
export type CaptionStyle = 'bold' | 'clean' | 'minimal';

export type Place = {
  frame: number; // when the label appears
  label: string; // "Ladakh, India"
  country: string | null; // world-atlas country name, for the map
  lat: number | null; // exact pin position when known (regions); else the country's centroid
  lon: number | null;
  showMap: boolean; // first time this country appears
  visited: string[]; // countries pinned before this one
  top?: number; // vertical: moved down below a face (share of the height); else under the top bar
  mapWidth?: number; // vertical: the map's width when moved (share of the width), shrunk to fit above the captions
  until?: number; // last frame of a moved map or label (for the captions)
};

export type Props = {
  width: number;
  height: number;
  fps: number;
  durationInFrames: number;
  transitionFrames: number;
  introFrames: number; // 0 = no intro
  outroFrames: number; // 0 = no outro
  font: string;
  channel: string;
  tagline: string;
  scenes: Scene[];
  captions: CaptionLine[];
  captionStyle: CaptionStyle;
  places: Place[];
};
