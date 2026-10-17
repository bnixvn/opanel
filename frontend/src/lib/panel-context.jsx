import { createContext, useContext } from 'react';

// What the page components read from App: state, setters and handlers, as
// SNPanel's pages read them (operator, 2026-10-17: the panel laid out as
// SNPanel). The pages used to be render functions inside App; this carries
// the names they need across a module boundary.
export const PanelContext = createContext(null);

export function usePanel() {
  const value = useContext(PanelContext);
  if (!value) throw new Error('usePanel() outside PanelContext');
  return value;
}

// A link that opens a page in place, and in a new browser tab on a
// Ctrl-, Cmd- or middle-click as any link does.
export function followInPanel(event, open) {
  if (event.defaultPrevented || event.button !== 0) return;
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  open();
}
