import { useState, type ReactNode } from "react";
import type { Game, WeatherFit } from "../api";
import { IconAlertTriangle } from "@tabler/icons-react";
import { SkyGlyph } from "./icons";
import { compassPoint, kickoff, skyIcon, timeAgo, wxEffect } from "../format";

/** Wind rose: the arrow shows where the wind is blowing to, with the speed in the middle. */
function Compass({ dir, speed }: { dir: number | null | undefined; speed: number }) {
  const level = speed >= 15 ? "strong" : speed >= 10 ? "breezy" : "calm";
  return (
    <svg className={`gw-compass ${level}`} viewBox="0 0 120 120" role="img"
      aria-label={`Wind ${Math.round(speed)} mph${dir != null ? ` from the ${compassPoint(dir)}` : ""}`}>
      <circle cx="60" cy="60" r="50" className="ring" />
      {Array.from({ length: 16 }, (_, i) => (
        <line key={i} x1="60" y1={i % 4 ? 13 : 11} x2="60" y2={i % 4 ? 17 : 20} className="tick" transform={`rotate(${i * 22.5} 60 60)`} />
      ))}
      {(["N", "E", "S", "W"] as const).map((p, i) => {
        const a = (i * Math.PI) / 2;
        return <text key={p} x={60 + 33 * Math.sin(a)} y={60 - 33 * Math.cos(a) + 4} className="pt">{p}</text>;
      })}
      {dir != null && (
        <g transform={`rotate(${dir + 180} 60 60)`}>
          <line x1="60" y1="98" x2="60" y2="26" className="arrow" />
          <path d="M60 14 L68 30 L60 26 L52 30 Z" className="head" />
        </g>
      )}
      <circle cx="60" cy="60" r="17" className="hub" />
      <text x="60" y="61" className="spd">{Math.round(speed)}</text>
      <text x="60" y="72" className="unit">mph</text>
    </svg>
  );
}

/** A stadium outline: a closed dome, or a retractable roof drawn with its panels parted. */
function Roof({ kind }: { kind: "dome" | "retractable" }) {
  return (
    <svg className="gw-roof-art" viewBox="0 0 140 80" role="img" aria-label={kind === "dome" ? "Domed stadium" : "Retractable-roof stadium"}>
      <path d="M10 66 L130 66" className="base" />
      <path d="M18 66 L18 46 L122 46 L122 66" className="bowl" />
      {kind === "dome" ? (
        <path d="M14 46 Q70 2 126 46 Z" className="roof" />
      ) : (
        <>
          <path d="M14 46 Q38 20 62 16 L62 46 Z" className="roof" />
          <path d="M78 16 Q102 20 126 46 L78 46 Z" className="roof" />
          <path d="M66 28 L56 28 M60 24 L56 28 L60 32 M74 28 L84 28 M80 24 L84 28 L80 32" className="slide" />
        </>
      )}
    </svg>
  );
}

const ROOF_TEXT: Record<string, [string, string]> = {
  retractable: ["Retractable roof", "Opened or closed on game day, so the model makes no weather adjustment."],
  closed: ["Roof closed", "Played indoors: no weather adjustment."],
  dome: ["Dome", "Played indoors: no weather adjustment."],
};

/** A game Overview's weather on one line: sky and temperature, wind, rain and the model's adjustment, or the roof. Opens into
 *  the full card (compass, gusts, the adjustment's reason). */
function WeatherStrip({ game, onOpen, focus }: { game: Game; onOpen: () => void; focus: boolean }) {
  const w = game.weather;
  let main: ReactNode, effect: ReactNode = null;
  if (!w) main = <span className="mut">No weather for this run yet</span>;
  else if (w.indoor) main = <><b>{(ROOF_TEXT[w.roof] ?? ["Indoors"])[0]}</b> <span className="mut">no weather adjustment</span></>;
  else if (w.source !== "forecast" || w.temp == null || w.wind == null) main = <span className="mut">Open air · forecast not available yet</span>;
  else {
    const air = wxEffect(w.factors.pass), rush = wxEffect(w.factors.rush);
    main = <>
      <span className="gw-strip-ico"><SkyGlyph name={skyIcon(w.sky, game.gametime)} /></span>
      <b>{Math.round(w.temp)}°F</b> <span className="mut">{w.sky ?? "Outdoors"}</span>
      <span className="gw-strip-dot">·</span><span>{Math.round(w.wind)} mph{w.wind_dir != null && ` ${compassPoint(w.wind_dir)}`}</span>
      {w.precip_prob != null && <><span className="gw-strip-dot">·</span><span>{Math.round(w.precip_prob)}% rain</span></>}
    </>;
    effect = air || rush
      ? <span className="gw-strip-fx">{air && <b className={w.factors.pass! < 1 ? "under" : "over"}>passing {air}</b>}{air && rush && " · "}{rush && <b className={w.factors.rush! > 1 ? "over" : "under"}>rushing {rush}</b>}</span>
      : <span className="gw-strip-fx mut">no adjustment</span>;
  }
  return (
    <button type="button" className="card gw-strip" onClick={onOpen} aria-expanded={false} autoFocus={focus} aria-label={`Weather for ${game.label}: show details`}>
      <span className="gw-strip-main">{main}</span>{effect}<span className="gw-strip-more mut">Details</span>
    </button>
  );
}

/** The selected game's kickoff weather, or its roof, shown above the props and projections for that game. compact: a one-line
 *  strip that opens into the card (a game's Overview). */
export default function GameWeather({ game, fit, compact = false }: { game: Game; fit?: WeatherFit | null; compact?: boolean }) {
  const [open, setOpen] = useState(false);
  // focus follows the toggle once the reader has used it, and never moves on its own when the page opens
  const [toggled, setToggled] = useState(false);
  const toggle = (o: boolean) => { setOpen(o); setToggled(true); };
  if (compact && !open) return <WeatherStrip game={game} onOpen={() => toggle(true)} focus={toggled} />;
  return <WeatherCard game={game} fit={fit} onClose={compact ? () => toggle(false) : undefined} />;
}

function WeatherCard({ game, fit, onClose }: { game: Game; fit?: WeatherFit | null; onClose?: () => void }) {
  const w = game.weather;
  const head = (
    <div className="gw-head">
      <b>{game.label}</b>
      <span className="mut">{[w?.stadium, kickoff(game.gameday, game.gametime)].filter(Boolean).join(" · ")}</span>
      {onClose && <button type="button" className="link gw-less" onClick={onClose} aria-expanded autoFocus aria-label="Show less weather">Less</button>}
    </div>
  );
  if (!w) return <section className="card gw" aria-label="Game weather">{head}<p className="mut">No weather for this run yet. A sync fetches it.</p></section>;

  if (w.indoor) {
    const [title, note] = ROOF_TEXT[w.roof] ?? ["Indoors", "No weather adjustment."];
    return (
      <section className="card gw" aria-label="Game weather">
        {head}
        <div className="gw-indoor">
          <Roof kind={w.roof === "retractable" ? "retractable" : "dome"} />
          <div><div className="gw-title">{title}</div><p className="mut">{note}</p></div>
        </div>
      </section>
    );
  }

  if (w.source !== "forecast" || w.temp == null || w.wind == null) {
    return (
      <section className="card gw" aria-label="Game weather">
        {head}
        <div className="gw-indoor">
          <span className="gw-big-ico"><SkyGlyph name="partly" /></span>
          <div>
            <div className="gw-title">Open-air stadium · forecast not available yet</div>
            <p className="mut">Forecasts reach about a week to 16 days ahead and are fetched on each sync.</p>
          </div>
        </div>
      </section>
    );
  }

  const air = wxEffect(w.factors.pass), rush = wxEffect(w.factors.rush);
  const gusty = w.gust != null && w.gust >= w.wind + 5;
  return (
    <section className="card gw" aria-label="Game weather">
      {head}
      <div className="gw-body">
        <div className="gw-now">
          <span className="gw-big-ico"><SkyGlyph name={skyIcon(w.sky, game.gametime)} /></span>
          <div>
            <div className="gw-temp">{Math.round(w.temp)}°F</div>
            <div className="mut">{w.sky ?? "Outdoors"}</div>
          </div>
        </div>
        <Compass dir={w.wind_dir} speed={w.wind} />
        <dl className="gw-stats">
          <div><dt>Wind</dt><dd>{Math.round(w.wind)} mph{w.wind_dir != null && <> from the {compassPoint(w.wind_dir)}</>}</dd></div>
          <div><dt>Gusts</dt><dd>{w.gust != null ? `${Math.round(w.gust)} mph` : "–"}{gusty && <IconAlertTriangle size={14} stroke={1.75} className="icon gust-warn" aria-label="gusty" />}</dd></div>
          {w.precip_prob != null || w.precip == null
            ? <div><dt>Rain chance</dt><dd>{w.precip_prob != null ? `${Math.round(w.precip_prob)}%` : "–"}</dd></div>
            : <div><dt>Rain</dt><dd>{w.precip < 0.01 ? "None" : `${w.precip.toFixed(2)}"`}</dd></div>}
          {(w.snow ?? 0) > 0.05 && <div><dt>Snow</dt><dd>{w.snow!.toFixed(1)}"</dd></div>}
        </dl>
      </div>
      <div className={`gw-effect${air || rush ? " on" : ""}`}>
        {air || rush ? (
          <>Model adjustment: <b className={air && w.factors.pass! < 1 ? "under" : "over"}>passing &amp; receiving {air ?? "±0%"}</b>
            {" · "}<b className={rush && w.factors.rush! > 1 ? "over" : ""}>rushing {rush ?? "±0%"}</b></>
        ) : (
          <>No adjustment: wind under {fit?.wind_from ?? 10} mph and warmer than {fit?.cold_from ?? 45}°F don't move yardage.</>
        )}
      </div>
      <p className="gw-foot mut">
        Average over the three hours from kickoff{w.provider ? ` · ${w.provider}` : ""}{w.fetched_at ? ` · fetched ${timeAgo(w.fetched_at)}` : ""}
      </p>
    </section>
  );
}
