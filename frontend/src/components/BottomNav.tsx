import { IconChartDots, IconChartLine, IconDots, IconFirstAidKit, IconFlame, IconGift, IconLayoutGrid, IconListDetails, IconReceipt, IconScale, IconSettings,
  IconStack2, type Icon } from "@tabler/icons-react";
import { useBetSlip } from "../BetSlipContext";
import Menu, { MenuItem } from "./Menu";
import { routeClick } from "./NavLink";

const ICON: Record<string, Icon> = { games: IconLayoutGrid, picks: IconListDetails, hot: IconFlame, parlays: IconStack2, projections: IconChartLine,
  injuries: IconFirstAidKit, compare: IconScale, model: IconChartDots, check: IconChartDots, admin: IconSettings, promos: IconGift };
const SHORT: Record<string, string> = { picks: "Props" };
/** Sections that get their own slot; the rest go under More (five slots in all, with the slip). */
const SLOTS = 3;

/** Phones: the main sections and the bet slip along the bottom, where a thumb reaches. */
export default function BottomNav<T extends string>({ tabs, tab, onTab, href }: { tabs: [T, string][]; tab?: T; onTab?: (t: T) => void; href?: (t: T) => string }) {
  const { legs, setOpen, clashCount } = useBetSlip();
  const main = tabs.slice(0, SLOTS);
  const rest = tabs.slice(SLOTS);
  const inRest = rest.find(([k]) => k === tab);
  const item = (k: T, label: string) => {
    const I = ICON[k] ?? IconDots;
    return (
      <a key={k} href={href?.(k)} className={`bnav-item${tab === k ? " on" : ""}`} aria-current={tab === k ? "page" : undefined} onClick={routeClick(() => onTab?.(k))}>
        <I size={22} stroke={1.75} aria-hidden /><span>{SHORT[k] ?? label}</span>
      </a>
    );
  };
  return (
    <nav className="bnav" aria-label="Sections">
      {main.map(([k, label]) => item(k, label))}
      <button className="bnav-item" onClick={() => setOpen(true)} aria-label={`Betslip, ${legs.length} ${legs.length === 1 ? "selection" : "selections"}${clashCount ? `, ${clashCount} to fix` : ""}`}>
        <span className="bnav-ico"><IconReceipt size={22} stroke={1.75} aria-hidden />{legs.length > 0 && <span className={`bnav-badge${clashCount ? " clash" : ""}`}>{clashCount ? "!" : legs.length}</span>}</span>
        <span>Slip</span>
      </button>
      <Menu placement="up" align="end" buttonClass={`bnav-item${inRest ? " on" : ""}`} caret={false}
        label={<><IconDots size={22} stroke={1.75} aria-hidden /><span>{inRest ? SHORT[inRest[0]] ?? inRest[1] : "More"}</span></>}>
        {(close) => rest.map(([k, label]) => {
          const I = ICON[k] ?? IconDots;
          return <MenuItem key={k} on={tab === k} href={href?.(k)} onClick={() => { close(); onTab?.(k); }}><span className="with-icon"><I size={18} stroke={1.75} aria-hidden />{label}</span></MenuItem>;
        })}
      </Menu>
    </nav>
  );
}
