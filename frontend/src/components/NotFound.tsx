import { routeClick } from "./NavLink";
import PageHead from "./PageHead";

/** A path that names no section (a typo, an old link): say so, with the way back, rather than quietly showing another page. */
export default function NotFound({ home, homeLabel, onHome }: { home: string; homeLabel: string; onHome: () => void }) {
  return (
    <>
      <PageHead title="Page not found" />
      <div className="card prefs-card">
        <p className="mut">There's no page at <code>{window.location.pathname}</code>. The link may be old, or mistyped.</p>
        <div className="prefs-actions">
          <a className="primary" href={home} onClick={routeClick(onHome)}>{homeLabel}</a>
        </div>
      </div>
    </>
  );
}
