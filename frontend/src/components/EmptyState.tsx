import type { Icon } from "@tabler/icons-react";
import { useEffect, type CSSProperties, type ReactNode } from "react";
import { kickoff } from "../format";
import type { League } from "../league";
import TeamLogo from "./TeamLogo";

/** An action that may remove its own button asks for the card with this id to take focus, if the board is still empty once
 *  it settles (the card may unmount for a loading skeleton and come back). Only while focus has fallen to the page. */
let pending: { id: string; until: number } | null = null;
const lost = () => document.activeElement === document.body || !document.activeElement?.isConnected;
export function focusEmpty(id: string) {
  pending = { id, until: Date.now() + 5000 };
  requestAnimationFrame(() => claim(id));
}
function claim(id: string) {
  const el = document.getElementById(id);
  if (!el || pending?.id !== id || Date.now() > pending.until) return;
  pending = null;
  if (lost()) el.focus();
}

/** Games the board will fill in, shown faded under the message. */
export interface PreviewGame { game_id: string; label: string; gameday: string; gametime: string }

/** Games past this many fold into "+ N more", so the preview hints at the board rather than standing in for it. */
const PREVIEW_ROWS = 4;

/** A board with nothing to show: what's missing (title), why and when it changes (body), an optional way out (action),
 *  and, when the board is only waiting on data for games we already know, those games faded underneath with a dash in
 *  each column still to come. The preview is still and labeled, unlike the shimmering loading Skeleton, and hidden
 *  from screen readers, which hear the message alone. compact: a quieter card for one section of a page (a parlay row,
 *  a game page's card) rather than a whole board. id: makes the card focusable, so an action that removes its own button
 *  (with the board still empty) can hand focus to the card instead of dropping it (see focusEmpty). */
export default function EmptyState({ icon: I, title, children, action, preview, columns = [], league, compact = false, id }: {
  icon?: Icon; title: ReactNode; children?: ReactNode; action?: ReactNode;
  preview?: PreviewGame[]; columns?: string[]; league?: League; compact?: boolean; id?: string;
}) {
  useEffect(() => { if (id) claim(id); }, [id]);
  const rows = preview?.slice(0, PREVIEW_ROWS) ?? [];
  const more = (preview?.length ?? 0) - rows.length;
  return (
    <section id={id} tabIndex={id ? -1 : undefined} className={`card empty-state${compact ? " compact" : ""}`}>
      <div className="es-msg">
        {I && <span className="es-icon" aria-hidden><I size={20} stroke={1.75} /></span>}
        <div role="status">
          <h3 className="es-title">{title}</h3>
          {children && <p className="es-body">{children}</p>}
        </div>
      </div>
      {action && <div className={`es-action${I ? " es-indent" : ""}`}>{action}</div>}
      {rows.length > 0 && (
        <div className="es-preview" aria-hidden style={{ "--es-cols": columns.length } as CSSProperties}>
          {columns.length > 0 && <div className="es-row es-cols"><span />{columns.map((c) => <span key={c}>{c}</span>)}</div>}
          {rows.map((g) => {
            const [, , away, home] = g.game_id.split("_");
            return (
              <div className="es-row" key={g.game_id}>
                <span className="es-game">
                  <span className="es-logos"><TeamLogo team={away} league={league} size={20} /><TeamLogo team={home} league={league} size={20} /></span>
                  <span><b>{g.label}</b><br /><span className="mut">{kickoff(g.gameday, g.gametime)}</span></span>
                </span>
                {columns.map((c) => <span key={c} className="es-dash">—</span>)}
              </div>
            );
          })}
          {more > 0 && <div className="es-more mut">+ {more} more</div>}
        </div>
      )}
    </section>
  );
}
