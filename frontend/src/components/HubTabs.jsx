import Tabs from './Tabs.jsx';
import { useT } from '../lib/t.js';

// The tab row of a hub (lib/navigation.jsx HUBS): the shared <Tabs>, in its
// link mode - each tab a page with an address of its own, so a tab is a link
// and a Ctrl-click opens it in a new browser tab. The open tab is kept in
// sight on a narrow screen, where the row scrolls with a fade at its ends.
export default function HubTabs({ found, page, navigateToPage, routeForPage }) {
  const t = useT();
  if (!found || found.tabs.length < 2) return null;
  return <Tabs className="hub-tabs" label={t(found.hub.title)} value={found.tab?.page || page}
    onChange={(next) => navigateToPage(next)}
    tabs={found.tabs.map((tab) => ({ id: tab.page, label: t(tab.label), href: routeForPage(tab.page) }))}/>;
}
