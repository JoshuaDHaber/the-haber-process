import type { ReactNode } from "react";

/** Phone-only sort chips: the table header (and its sortable columns) is hidden when rows become cards. */
export default function SortBar({ options, toggle, arrow }: { options: [string, string][]; toggle: (k: string) => void; arrow: (k: string) => string }) {
  return (
    <div className="sortbar" role="group" aria-label="Sort by">
      <span className="mut">Sort</span>
      {options.map(([k, label]) => (
        <button key={k} className={arrow(k) ? "on" : ""} onClick={() => toggle(k)}>{label}{arrow(k)}</button>
      ))}
    </div>
  );
}

/** A sortable column header (wide screens; phones use SortBar). The label is a button, so the keyboard can sort too, and the
 *  sorted column carries aria-sort. extra: what follows the label inside the header but outside the button (an Info term). */
export function SortTh({ k, label, title, extra, toggle, arrow, sortOf }: { k: string; label: ReactNode; title?: string; extra?: ReactNode;
  toggle: (k: string) => void; arrow: (k: string) => string; sortOf: (k: string) => "ascending" | "descending" | undefined }) {
  return (
    <th scope="col" aria-sort={sortOf(k)} title={title} className="sortable">
      <button type="button" className="th-sort" onClick={() => toggle(k)}>{label}<span aria-hidden>{arrow(k)}</span></button>
      {extra && <> {extra}</>}
    </th>
  );
}
