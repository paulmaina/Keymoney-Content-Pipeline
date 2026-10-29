// Props written by remotion_render.py. All times are in frames on the final timeline.

export type Move = {
  // start and end of the camera: zoom (1 = image just covers the frame) and offset as a % of the frame
  from: {scale: number; x: number; y: number};
  to: {scale: number; x: number; y: number};
  name: string;
};

export type Scene = {
  frames: number; // sequence length, including the transition overlap on each side
  kind: 'image' | 'card';
  src?: string; // image file in the public dir
  width?: number; // image size, so the mask lines up with the cover-cropped picture
  height?: number;
  mask?: string; // foreground mask for the 2.5D parallax
  text?: string; // title card text
  move: Move;
};

export type CaptionWord = {text: string; start: number; end: number};
export type CaptionLine = {start: number; end: number; words: CaptionWord[]};

export type Place = {
  frame: number; // when the label appears
  label: string; // "Ladakh, India"
  country: string | null; // world-atlas country name, for the map
  lat: number | null; // exact pin position when known (regions); else the country's centroid
  lon: number | null;
  showMap: boolean; // first time this country appears
  visited: string[]; // countries pinned before this one
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
  places: Place[];
};
