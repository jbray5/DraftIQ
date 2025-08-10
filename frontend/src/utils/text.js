export const normText = (s = '') =>
  s.toLowerCase().replace(/\s+/g, ' ').replace(/\u00A0/g, ' ').trim();
