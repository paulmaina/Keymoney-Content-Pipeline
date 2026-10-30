// Smart Village Boy colours: the red cap and yellow T-shirt, on a deep evening blue.
export const BRAND = {
  red: '#E63946',
  yellow: '#FFD23F',
  night: '#0E2A3B',
  nightLight: '#1C4A63',
  white: '#FFFFFF',
  sky: '#8FD8FF', // key words in the calmer caption styles, apart from the yellow spoken word
};

export const FONT = 'BrandFont';

// a black outline that reads on any picture (text-shadow, since -webkit-text-stroke eats into the letters)
export const outline = (px: number) =>
  [
    [px, 0], [-px, 0], [0, px], [0, -px],
    [px * 0.7, px * 0.7], [-px * 0.7, px * 0.7], [px * 0.7, -px * 0.7], [-px * 0.7, -px * 0.7],
  ]
    .map(([x, y]) => `${x}px ${y}px 0 #000`)
    .join(', ') + `, 0 ${px}px ${px * 3}px rgba(0,0,0,0.55)`;
