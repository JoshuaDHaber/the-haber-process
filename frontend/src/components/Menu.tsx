import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { routeClick } from "./NavLink";

export const Caret = () => (
  <svg className="caret" width="10" height="6" viewBox="0 0 10 6" aria-hidden="true">
    <path d="M1 1l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

interface Props {
  label: ReactNode;
  ariaLabel?: string;
  /** The button's class: "chip-menu" (a small pill) by default. */
  buttonClass?: string;
  /** Which edge of the button the popover lines up with. */
  align?: "start" | "end";
  /** Open below the button, or above it (the phone's bottom bar). */
  placement?: "down" | "up";
  caret?: boolean;
  children: (close: () => void) => ReactNode;
}

/** The popover's items, in order: menu items first, else anything focusable (a checkbox, a slider). */
const itemsIn = (pop: HTMLElement | null) => {
  if (!pop) return [];
  const items = [...pop.querySelectorAll<HTMLElement>(".menu-item:not(:disabled)")];
  return items.length ? items : [...pop.querySelectorAll<HTMLElement>("a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled)")];
};

/** A button that opens a small popover below it; a click outside or Escape closes it. A disclosure, not an ARIA menu: some
 *  popovers hold checkboxes and sliders too. Opened from the keyboard, focus moves to the current choice (or the first item);
 *  up and down arrows, Home and End move through the items; Escape, or picking an item, puts focus back on the button. */
export default function Menu({ label, ariaLabel, buttonClass = "chip-menu", align = "end", placement = "down", caret = true, children }: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const pop = useRef<HTMLDivElement>(null);
  const fromKeys = useRef(false);
  const id = useId();
  useEffect(() => {
    if (!open) return;
    const outside = (e: PointerEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("pointerdown", outside);
    if (fromKeys.current) {
      const items = itemsIn(pop.current);
      (items.find((i) => i.getAttribute("aria-pressed") === "true" || i.getAttribute("aria-current")) ?? items[0])?.focus();
    }
    return () => document.removeEventListener("pointerdown", outside);
  }, [open]);
  // an item's action closes the menu: focus goes back to the button, unless the action moved it somewhere on purpose
  // (an edit form's first field) or took the button away
  const close = () => {
    setOpen(false);
    requestAnimationFrame(() => {
      const a = document.activeElement;
      if ((!a || a === document.body || pop.current?.contains(a) || !a.isConnected) && button.current?.isConnected) button.current.focus();
    });
  };
  const onKey = (e: KeyboardEvent) => {
    if (!open) return;
    if (e.key === "Escape") { e.stopPropagation(); setOpen(false); button.current?.focus(); return; }
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) return;
    const items = itemsIn(pop.current);
    if (!items.length) return;
    // a slider or text field keeps its own arrow keys
    const t = e.target as HTMLElement;
    if (t.tagName === "INPUT" && (t as HTMLInputElement).type !== "checkbox") return;
    e.preventDefault();
    const at = items.indexOf(document.activeElement as HTMLElement);
    const next = e.key === "Home" ? 0 : e.key === "End" ? items.length - 1
      : e.key === "ArrowDown" ? (at + 1) % items.length : (at <= 0 ? items.length : at) - 1;
    items[next].focus();
  };
  return (
    <div className="menu" ref={ref} onKeyDown={onKey}>
      <button ref={button} className={buttonClass} aria-expanded={open} aria-controls={open ? id : undefined} aria-label={ariaLabel}
        onClick={(e) => { fromKeys.current = e.detail === 0; setOpen(!open); }}
        onKeyDown={(e) => { if (!open && e.key === "ArrowDown") { e.preventDefault(); fromKeys.current = true; setOpen(true); } }}>
        {label}{caret && <Caret />}
      </button>
      {open && <div id={id} ref={pop} className={`menu-pop ${align} ${placement}`}>{children(close)}</div>}
    </div>
  );
}

/** One item in a Menu. A choice (`on` given) says whether it's the current one; an action (`on` left out) says nothing, and
 *  `danger` marks one that can't be taken back. With href it's a link (a section), which a plain click still routes through
 *  onClick. */
export function MenuItem({ on, danger, onClick, href, children }: { on?: boolean; danger?: boolean; onClick: () => void; href?: string; children: ReactNode }) {
  if (href) return (
    <a className="menu-item" href={href} aria-current={on ? "page" : undefined} onClick={routeClick(onClick)}>
      <span>{children}</span>{on && <span className="menu-check" aria-hidden="true">✓</span>}
    </a>
  );
  return (
    <button type="button" className={`menu-item${danger ? " danger" : ""}`} aria-pressed={on === undefined ? undefined : on} onClick={onClick}>
      <span>{children}</span>{on && <span className="menu-check" aria-hidden="true">✓</span>}
    </button>
  );
}
