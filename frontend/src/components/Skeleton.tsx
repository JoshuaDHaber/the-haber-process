/** Gray placeholders shaped like what's loading, so the page doesn't jump when it arrives. tiles: a sideways row of parlay
 *  tiles. card: one win-chance card (header, bar, a row per team). label: what screen readers hear while it loads. */
export default function Skeleton({ kind, count, label = "Loading…" }: { kind: "rows" | "cards" | "page" | "panel" | "tiles" | "card"; count?: number; label?: string }) {
  const n = (d: number) => Array.from({ length: count ?? d }, (_, i) => i);
  return (
    <div className={`skel skel-${kind}`} aria-busy="true" aria-live="polite">
      <span className="sr-only">{label}</span>
      {kind === "rows" && <div className="card skel-table">{n(6).map((i) => (
        <div key={i} className="skel-row"><i className="skel-dot" /><span><i style={{ width: "42%" }} /><i style={{ width: "26%" }} /></span><i className="skel-pill" /><i className="skel-num" /></div>
      ))}</div>}
      {kind === "cards" && <div className="skel-grid">{n(3).map((i) => (
        <div key={i} className="card skel-card"><i style={{ width: "40%" }} /><i className="skel-big" /><i /><i /><i style={{ width: "70%" }} /></div>
      ))}</div>}
      {kind === "page" && <>
        <div className="skel-bar"><i className="skel-logo" /><i style={{ width: 140 }} /><span /><i className="skel-pill" /><i className="skel-pill" /></div>
        <i className="skel-title" />
        <div className="skel-grid">{n(6).map((i) => <div key={i} className="card skel-card"><i style={{ width: "50%" }} /><i className="skel-big" /><i /><i style={{ width: "60%" }} /></div>)}</div>
      </>}
      {kind === "tiles" && <div className="skel-tiles">{n(3).map((i) => (
        <div key={i} className="card skel-tile"><i style={{ width: "55%" }} /><i style={{ width: "80%" }} /><i className="skel-big" />
          <div className="skel-stats"><i /><i /><i /></div>
          {[0, 1, 2, 3].map((j) => <div key={j} className="skel-leg"><i className="skel-dot" /><span><i style={{ width: "62%" }} /><i style={{ width: "38%" }} /></span></div>)}
          <i className="skel-btn" /></div>
      ))}</div>}
      {kind === "card" && <div className="card skel-card"><i style={{ width: "45%" }} /><i className="skel-wide" />
        {n(2).map((i) => <div key={i} className="skel-row"><i className="skel-dot" /><span><i style={{ width: "50%" }} /><i style={{ width: "30%" }} /></span><i className="skel-pill" /><i className="skel-num" /></div>)}
        <i style={{ width: "60%" }} /></div>}
      {kind === "panel" && <div className="skel-panel"><div className="skel-head"><i className="skel-avatar" /><span><i style={{ width: 180 }} /><i style={{ width: 120 }} /></span></div>
        <i className="skel-chart" />{n(4).map((i) => <i key={i} />)}</div>}
    </div>
  );
}
