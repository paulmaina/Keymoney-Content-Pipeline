// Props written by remotion_render.py. All times are in frames on the final timeline.

export type Move = {
  // start and end of the camera: zoom (1 = image just covers the frame) and offset as a % of the frame
  from: {scale: number; x: number; y: number};
  to: {scale: number; x: number; y: number};
  name: string;
};

export type Shot = {
  src: string;
  width: number; // image size, so the mask lines up with the cover-cropped picture
  height: number;
  mask?: string; // foreground mask for the 2.5D parallax
  plate?: string; // the picture with its foreground filled in (strong depth): what the moving subject uncovers
  move: Move;
  from: number; // first frame of this shot within the scene
};

export type Parallax = {scale: number; shift: number; back: number}; // near layer growth, its drift %, backdrop drift %

export type Overlay = 'dust' | 'smoke' | 'mist' | 'rain' | 'heat' | 'fire' | 'leaves' | 'ripple' | 'rays';

export type Scene = {
  frames: number; // sequence length, including the transition overlap on each side
  kind: 'image' | 'video' | 'card';
  src?: string; // image, or a stock clip already cut to the scene's length
  width?: number;
  height?: number;
  mask?: string;
  text?: string; // title card text
  move: Move;
  shots?: Shot[]; // pictures: one, or two of the same moment that the scene cuts between
  parallax?: Parallax | null;
  overlays?: Overlay[];
  ease?: number[]; // cubic-bezier of the camera move
  transition?: 'fade' | 'whip' | 'zoom'; // into this scene
  seed?: number; // varies the overlays from scene to scene
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
  part?: Part | null; // a vertical Shorts series: "Part 1 of N" in the intro, "Watch Part N+1" in the outro
  introClip?: string | null; // the channel's own intro clip (already fitted to the frame), instead of the drawn one
  outroClip?: string | null; // the channel's own outro clip
  brand?: Brand | null; // the channel's colours for the drawn intro and outro
};

export type Brand = {red: string; yellow: string; night: string; nightLight: string};

export type Part = {index: number; count: number};
