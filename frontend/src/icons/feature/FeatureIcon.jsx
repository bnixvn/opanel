import { createElement, useId } from 'react';
import { FEATURE_ICONS } from './icons.js';
import './FeatureIcon.css';

// A feature's picture: <FeatureIcon name="websites" size={40} />.
// - Inline SVG on a 48 grid, coloured by FeatureIcon.css's palette, so it
//   follows the theme.
// - variant="mono" draws it in the text colour (currentColor), the soft
//   tints at 40% and the paper and ink parts cut out: for small places
//   such as a header beside a title.
// - muted greys it out: a feature that is not installed yet.
// - Decorative (aria-hidden) unless given a title, which becomes its name.
// The small glyphs of buttons and menus stay lucide-react.

const TAGS = { R: 'rect', C: 'circle', E: 'ellipse', P: 'path' };
const ATTRS = { R: ['x', 'y', 'width', 'height', 'rx'], C: ['cx', 'cy', 'r'], E: ['cx', 'cy', 'rx', 'ry'] };

// One shape, as { tag, classes, attrs }.
function parseShape(text) {
  const [type, cls, ...rest] = text.trim().split(' ');
  const attrs = type === 'P' ? { d: rest.join(' ') } : Object.fromEntries(rest.map((value, i) => [ATTRS[type][i], value]));
  return { tag: TAGS[type], classes: cls.split('.'), attrs };
}

const parsed = new Map();
function partsOf(name) {
  if (!parsed.has(name)) {
    const [main, badge = ''] = FEATURE_ICONS[name].split('#');
    const list = (part) => part.split(';').filter(Boolean).map(parseShape);
    parsed.set(name, [list(main), list(badge)]);
  }
  return parsed.get(name);
}

const isColour = (c) => c !== 'j' && c !== 'd';
const isStroke = (c) => c.startsWith('x');
const isCutter = (c) => /^x?[kp]$/.test(c);
// A colour class in the mono variant: one colour, tints kept as tints.
const monoClass = (c) => (isColour(c) ? `${isStroke(c) ? 'x' : ''}q${c.endsWith('2') ? '2' : ''}` : c);

const shape = (s, key, className) => createElement(s.tag, { key, className, ...s.attrs });

// The shapes of one part (the drawing, or its badge). In mono, a paper or
// ink shape cuts through whatever was drawn before it.
function drawPart(shapes, mono, prefix, defs) {
  let layer = [];
  shapes.forEach((s, i) => {
    if (mono && s.classes.some(isCutter)) {
      const id = `${prefix}c${i}`;
      const stroke = s.classes.some(isStroke);
      const extra = s.classes.filter((c) => !isColour(c)).join(' ');
      defs.push(<mask key={id} id={id} maskUnits="userSpaceOnUse" x="0" y="0" width="48" height="48">
        <rect className="fi-mw" width="48" height="48"/>
        {shape(s, 0, `${stroke ? 'fi-mcs' : 'fi-mc'} ${extra}`)}
      </mask>);
      layer = [<g key={id} mask={`url(#${id})`}>{layer}</g>];
    } else {
      layer.push(shape(s, `${prefix}${i}`, (mono ? s.classes.map(monoClass) : s.classes).join(' ')));
    }
  });
  return layer;
}

// The badge's silhouette, 2 units wider all round, for the cut under it.
function cutClass(s) {
  const colours = s.classes.filter(isColour);
  const filled = colours.some((c) => !isStroke(c));
  return `${filled ? 'fi-mk' : 'fi-mkn'}${colours.some(isStroke) ? ' fi-mks' : ''}`;
}

export function FeatureIcon({ name, size = 48, title, variant = 'color', muted = false, className = '', ...rest }) {
  const uid = `fi${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`;
  let known = name;
  if (!Object.prototype.hasOwnProperty.call(FEATURE_ICONS, name)) {
    if (import.meta.env.DEV) console.warn(`FeatureIcon: no icon named "${name}"`);
    known = 'generic';
  }
  const mono = variant === 'mono';
  const [main, badge] = partsOf(known);
  const defs = [];
  let drawing = drawPart(main, mono, `${uid}m`, defs);
  if (badge.length > 0) {
    defs.push(<mask key="badge" id={`${uid}b`} maskUnits="userSpaceOnUse" x="0" y="0" width="48" height="48">
      <rect className="fi-mw" width="48" height="48"/>
      {badge.map((s, i) => shape(s, i, cutClass(s)))}
    </mask>);
    drawing = [<g key="main" mask={`url(#${uid}b)`}>{drawing}</g>, ...drawPart(badge, mono, `${uid}b`, defs)];
  }
  const label = title ? { role: 'img', 'aria-label': title } : { 'aria-hidden': 'true' };
  const classes = ['feature-icon', mono && 'is-mono', muted && 'is-muted', className].filter(Boolean).join(' ');
  return <svg viewBox="0 0 48 48" width={size} height={size} focusable="false" className={classes} data-icon={known} {...label} {...rest}>
    {title && <title>{title}</title>}
    {defs.length > 0 && <defs>{defs}</defs>}
    {drawing}
  </svg>;
}

export default FeatureIcon;
