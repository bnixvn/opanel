// The WordPress Manager's glyph (SNPanel's icons/WordPressGlyph.jsx): a "W"
// zigzag in a ring, drawn in lucide's grammar - a 24 grid, a 2-unit stroke
// with round ends, in currentColor - so it sits among the sidebar's lucide
// icons. A plain "W", not the WordPress logo. Takes what a lucide icon takes.
export default function WordPressGlyph({ size = 24, strokeWidth = 2, className = '', ...rest }) {
  return <svg xmlns="http://www.w3.org/2000/svg" width={size} height={size} viewBox="0 0 24 24" fill="none"
    stroke="currentColor" strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round"
    className={`lucide sn-glyph-wordpress ${className}`.trim()} aria-hidden="true" focusable="false" {...rest}>
    <circle cx="12" cy="12" r="10"/>
    <path d="M6 8l3.2 8.5L12 10.5l2.8 6L18 8"/>
  </svg>;
}
