"""SQLAlchemy models. Postgres in production and local dev; SQLite still works (the unit tests run on it in memory)."""
import json
import math
import os
import threading
import weakref
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, create_engine, event, false
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from . import config


class Base(DeclarativeBase):
    pass


class Run(Base):
    """One execution of the projection pipeline."""
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    season: Mapped[int] = mapped_column(Integer)
    week: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    backtest: Mapped[dict] = mapped_column(JSON)
    variance: Mapped[dict] = mapped_column(JSON)
    excluded: Mapped[list] = mapped_column(JSON, default=list)
    weather: Mapped[dict] = mapped_column(JSON, nullable=True)  # game_id -> roof, forecast and per-market multipliers
    teams: Mapped[dict] = mapped_column(JSON, nullable=True)    # game_id -> team ratings and win probability (engine/moneyline.py)
    projections: Mapped[list["Projection"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class Projection(Base):
    __tablename__ = "projections"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    player_id: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(80))
    pos: Mapped[str] = mapped_column(String(4))
    team: Mapped[str] = mapped_column(String(4))
    opp: Mapped[str] = mapped_column(String(4))
    home: Mapped[bool] = mapped_column(Boolean)
    kind: Mapped[str] = mapped_column(String(6))
    mu: Mapped[float] = mapped_column(Float)
    sd: Mapped[float] = mapped_column(Float)
    vol: Mapped[float] = mapped_column(Float)
    eff: Mapped[float] = mapped_column(Float)
    spread: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="")
    game_id: Mapped[str] = mapped_column(String(24), index=True)
    gameday: Mapped[str] = mapped_column(String(12))
    gametime: Mapped[str] = mapped_column(String(8), default="")
    last5: Mapped[list] = mapped_column(JSON)
    wx: Mapped[float] = mapped_column(Float, nullable=True, default=1.0)  # weather multiplier already in mu (1 = none)
    teammate_out: Mapped[str] = mapped_column(String(160), nullable=True, default="")  # see redistribute.teammates_out
    thin: Mapped[bool] = mapped_column(Boolean, nullable=True, default=False)  # thin history: shown, never priced (model.THIN_GAMES)
    qb_adj: Mapped[float] = mapped_column(Float, nullable=True)     # receiving cut for a downgrade at QB, already in mu (model.qb_fields)
    qb_name: Mapped[str] = mapped_column(String(80), nullable=True, default="")  # the starting QB behind qb_adj
    dvp: Mapped[float] = mapped_column(Float, nullable=True)        # matchup grade (engine/dvp.py): the defense's factor vs his position
    dvp_rank: Mapped[int] = mapped_column(Integer, nullable=True)   # 1 = the softest defense for his position and this market
    dvp_n: Mapped[int] = mapped_column(Integer, nullable=True)      # teams ranked
    run: Mapped[Run] = relationship(back_populates="projections")

    __table_args__ = (Index("ix_proj_run_kind", "run_id", "kind"),)


class OddsLine(Base):
    """One sportsbook's over/under quote. Rows from one fetch share fetched_at."""
    __tablename__ = "odds_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    player: Mapped[str] = mapped_column(String(80))
    market: Mapped[str] = mapped_column(String(6), index=True)
    line: Mapped[float] = mapped_column(Float)
    over_odds: Mapped[float] = mapped_column(Float, nullable=True)
    under_odds: Mapped[float] = mapped_column(Float, nullable=True)
    book: Mapped[str] = mapped_column(String(40))
    over_link: Mapped[str] = mapped_column(String(500), nullable=True)
    under_link: Mapped[str] = mapped_column(String(500), nullable=True)
    event_link: Mapped[str] = mapped_column(String(500), nullable=True)
    game_id: Mapped[str] = mapped_column(String(24), nullable=True, index=True)  # which game the quote is for (null: older rows)


class AltLine(Base):
    """Alternate-line quotes, fetched on demand for one game at a time."""
    __tablename__ = "alt_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime)
    game_id: Mapped[str] = mapped_column(String(24), index=True)
    player: Mapped[str] = mapped_column(String(80))
    market: Mapped[str] = mapped_column(String(6))
    line: Mapped[float] = mapped_column(Float)
    over_odds: Mapped[float] = mapped_column(Float, nullable=True)
    under_odds: Mapped[float] = mapped_column(Float, nullable=True)
    book: Mapped[str] = mapped_column(String(40))
    over_link: Mapped[str] = mapped_column(String(500), nullable=True)
    under_link: Mapped[str] = mapped_column(String(500), nullable=True)
    event_link: Mapped[str] = mapped_column(String(500), nullable=True)


class OddsHistory(Base):
    """Every quote from every fetch, main and alternate, never replaced or deleted. odds_lines and alt_lines keep only
    each game's latest fetch (what the board shows); this keeps how lines moved through the week and the last price
    before kickoff, to grade the model against the market once the games are played. No betslip links (they go stale
    and are most of a row's size)."""
    __tablename__ = "odds_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    game_id: Mapped[str] = mapped_column(String(24), nullable=True)
    player: Mapped[str] = mapped_column(String(80))
    market: Mapped[str] = mapped_column(String(6))
    line: Mapped[float] = mapped_column(Float)
    over_odds: Mapped[float] = mapped_column(Float, nullable=True)
    under_odds: Mapped[float] = mapped_column(Float, nullable=True)
    book: Mapped[str] = mapped_column(String(40))
    alt: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (Index("ix_odds_history_game_market", "game_id", "market"),)


class EmptyMarket(Base):
    """A market asked for one game that no book had posted (longest-rush alternates never are). A sync only asks
    again once the row is old (pipeline.EMPTY_RETRY_HOURS), so a market books don't offer can't mark the game as
    missing odds on every sync. Deleted when the market comes back with quotes."""
    __tablename__ = "empty_markets"
    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[str] = mapped_column(String(24), index=True)
    market: Mapped[str] = mapped_column(String(6))
    alt: Mapped[bool] = mapped_column(Boolean, default=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime)


class NbaRunTables(Base):
    """One NBA run's per-kind error tables (engine.model.fit_dist, plus "_doubles": nba/model.fit_doubles' curves), kept
    after the run itself is pruned (nba/pipeline.KEEP_RUNS keeps four days of runs). The pick logs name the run that
    priced each pick, so a later backtest of the board or the ladders prices with the exact tables it used."""
    __tablename__ = "nba_run_tables"
    run_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)   # nba_runs.id, not a foreign key: it outlives the run
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    tables: Mapped[dict] = mapped_column(JSON)


class NbaRun(Base):
    """One NBA model run: projections for the slate (the next two game dates), team ratings, backtest."""
    __tablename__ = "nba_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    season: Mapped[int] = mapped_column(Integer)
    slate: Mapped[list] = mapped_column(JSON, default=list)       # Eastern dates covered, YYYY-MM-DD
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    backtest: Mapped[dict] = mapped_column(JSON)
    variance: Mapped[dict] = mapped_column(JSON)                  # per-kind error tables (engine.model.fit_dist)
    excluded: Mapped[list] = mapped_column(JSON, default=list)
    games: Mapped[list] = mapped_column(JSON, default=list)       # the slate's games (nba/espn.parse_scoreboard rows)
    teams: Mapped[dict] = mapped_column(JSON, nullable=True)      # game_id -> ratings and win probability (nba/model.py)
    projections: Mapped[list["NbaProjection"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class NbaProjection(Base):
    """Same columns as Projection, so the shared pricing code and API schemas apply. vol = projected minutes,
    eff = per-minute rate (opponent-adjusted), spread = the team's expected margin from the market spread."""
    __tablename__ = "nba_projections"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("nba_runs.id", ondelete="CASCADE"), index=True)
    player_id: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(80))
    pos: Mapped[str] = mapped_column(String(4))
    team: Mapped[str] = mapped_column(String(5))
    opp: Mapped[str] = mapped_column(String(5))
    home: Mapped[bool] = mapped_column(Boolean)
    kind: Mapped[str] = mapped_column(String(6))
    mu: Mapped[float] = mapped_column(Float)
    sd: Mapped[float] = mapped_column(Float)
    vol: Mapped[float] = mapped_column(Float)
    eff: Mapped[float] = mapped_column(Float)
    spread: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="")
    game_id: Mapped[str] = mapped_column(String(32), index=True)
    gameday: Mapped[str] = mapped_column(String(12))
    gametime: Mapped[str] = mapped_column(String(8), default="")
    last5: Mapped[list] = mapped_column(JSON)
    wx: Mapped[float] = mapped_column(Float, nullable=True, default=1.0)   # always 1: no weather indoors
    teammate_out: Mapped[str] = mapped_column(String(160), nullable=True, default="")  # absent regulars in their first games out
    parts: Mapped[dict] = mapped_column(JSON, nullable=True)   # what mu is made of (nba/pipeline.py): minutes, rate, opponent, teammates out
    dvp: Mapped[float] = mapped_column(Float, nullable=True)        # matchup grade (engine/dvp.py): the defense's factor vs his position
    dvp_rank: Mapped[int] = mapped_column(Integer, nullable=True)   # 1 = the softest defense for his position and this market
    dvp_n: Mapped[int] = mapped_column(Integer, nullable=True)      # teams ranked
    run: Mapped[NbaRun] = relationship(back_populates="projections")


class NbaOddsLine(Base):
    """NBA quotes (points, rebounds, assists, moneyline), shaped like OddsLine; kept apart so the NFL board never sees them."""
    __tablename__ = "nba_odds_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    player: Mapped[str] = mapped_column(String(80))
    market: Mapped[str] = mapped_column(String(6), index=True)
    line: Mapped[float] = mapped_column(Float)
    over_odds: Mapped[float] = mapped_column(Float, nullable=True)
    under_odds: Mapped[float] = mapped_column(Float, nullable=True)
    book: Mapped[str] = mapped_column(String(40))
    over_link: Mapped[str] = mapped_column(String(500), nullable=True)
    under_link: Mapped[str] = mapped_column(String(500), nullable=True)
    event_link: Mapped[str] = mapped_column(String(500), nullable=True)
    game_id: Mapped[str] = mapped_column(String(32), nullable=True, index=True)


class NbaAltLine(Base):
    """NBA alternate-line quotes (the parlay ladders' rungs), shaped like AltLine; one snapshot per game and market."""
    __tablename__ = "nba_alt_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime)
    game_id: Mapped[str] = mapped_column(String(32), index=True)
    player: Mapped[str] = mapped_column(String(80))
    market: Mapped[str] = mapped_column(String(6))
    line: Mapped[float] = mapped_column(Float)
    over_odds: Mapped[float] = mapped_column(Float, nullable=True)
    under_odds: Mapped[float] = mapped_column(Float, nullable=True)
    book: Mapped[str] = mapped_column(String(40))
    over_link: Mapped[str] = mapped_column(String(500), nullable=True)
    under_link: Mapped[str] = mapped_column(String(500), nullable=True)
    event_link: Mapped[str] = mapped_column(String(500), nullable=True)


class Setting(Base):
    """A site-wide option set from the admin page (app/books.py's hide_offshore)."""
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, nullable=True)


class User(Base):
    """A signed-in visitor (app/auth.py): Google or an emailed link/code, matched on the verified email. No passwords."""
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)   # lowercased
    google_sub: Mapped[str] = mapped_column(String(64), unique=True, nullable=True)  # Google's stable id (an email can change)
    name: Mapped[str] = mapped_column(String(120), nullable=True)
    avatar_url: Mapped[str] = mapped_column(String(500), nullable=True)
    age_confirmed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)  # ticked "I'm 21 or older"
    prefs: Mapped[dict] = mapped_column(JSON, nullable=True)        # the preferences page: sportsbook, theme, ...
    favorites: Mapped[dict] = mapped_column(JSON, nullable=True)    # per league: {"nfl": {"teams": [...], "players": [...]}}
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_login_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)


class UserSession(Base):
    """A signed-in browser. The id is the sha256 of the cookie, so the table alone can't be used to sign in."""
    __tablename__ = "user_sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    user_agent: Mapped[str] = mapped_column(String(200), default="")


class LoginToken(Base):
    """One emailed sign-in: a link token and a 6-digit code, both stored hashed, good once for 15 minutes."""
    __tablename__ = "login_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    next_path: Mapped[str] = mapped_column(String(200), default="/")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    used_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)       # wrong codes; the code stops working after 5


class UserSlip(Base):
    """A signed-in visitor's betslip (legs and stakes per book), so it follows them between devices (app/slip_sync.py).
    The legs are the browser's own records (frontend slip.ts), kept as they come."""
    __tablename__ = "user_slips"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    legs: Mapped[list] = mapped_column(JSON, default=list)
    stakes: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Bet(Base):
    """A bet a signed-in visitor placed at a sportsbook and tracks here (app/bets.py): a single or a parlay of player props,
    settled from the box scores like the board. Private to its owner."""
    __tablename__ = "bets"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    league: Mapped[str] = mapped_column(String(3))                   # nfl | nba | oth (a custom bet on anything else)
    book: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(6))                     # single | parlay | custom (settled by hand)
    stake: Mapped[float] = mapped_column(Float)                      # dollars
    odds: Mapped[int] = mapped_column(Integer)                       # American; a parlay's whole price as recorded
    status: Mapped[str] = mapped_column(String(5), default="open")   # open | won | lost | push | void
    payout: Mapped[float] = mapped_column(Float, nullable=True)      # returned, stake included (0 for a loss)
    settled_by: Mapped[str] = mapped_column(String(8), nullable=True)  # grading | user
    notes: Mapped[str] = mapped_column(String(500), nullable=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)           # the bettor's own labels ("SGP", "Sunday"), for filtering
    description: Mapped[str] = mapped_column(String(200), nullable=True)   # a custom bet: what it was, as the bettor wrote it
    # placed with a sportsbook's bonus bet (free bet): the stake isn't the bettor's money, and a win pays only the profit
    bonus: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # recorded after its first game had started (up to bets.LATE_DAYS later): the bettor may have known how it went
    late: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # a profit boost, in percent (50 = the winnings are 1.5x what the odds pay): `odds` stays the price before the boost
    boost: Mapped[int] = mapped_column(Integer, nullable=True)
    # the most of the stake a boost covers ("boosted up to max $10 wager"); unset: all of it
    boost_cap: Mapped[float] = mapped_column(Float, nullable=True)
    # a no-sweat bet: the most the book refunds, as a bonus bet, if it loses (up to the stake)
    no_sweat: Mapped[float] = mapped_column(Float, nullable=True)
    # an early-win token on a moneyline: the lead (points) at which the book pays it as a win, whatever the final score
    early_win: Mapped[int] = mapped_column(Integer, nullable=True)
    placed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    settled_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    legs: Mapped[list["BetLeg"]] = relationship(back_populates="bet", cascade="all, delete-orphan", order_by="BetLeg.id")


class BetLeg(Base):
    """One player prop in a tracked bet, graded on its own (main or alternate line)."""
    __tablename__ = "bet_legs"
    id: Mapped[int] = mapped_column(primary_key=True)
    bet_id: Mapped[int] = mapped_column(ForeignKey("bets.id", ondelete="CASCADE"), index=True)
    game_id: Mapped[str] = mapped_column(String(32), index=True)   # NBA ids run longer than the NFL's
    gameday: Mapped[str] = mapped_column(String(12))
    kickoff: Mapped[datetime] = mapped_column(DateTime)              # UTC
    player_id: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(80))
    team: Mapped[str] = mapped_column(String(4))
    opp: Mapped[str] = mapped_column(String(4))
    kind: Mapped[str] = mapped_column(String(6))
    side: Mapped[str] = mapped_column(String(5))
    line: Mapped[float] = mapped_column(Float)
    odds: Mapped[int] = mapped_column(Integer)
    alt: Mapped[bool] = mapped_column(Boolean, default=False)
    prob: Mapped[float] = mapped_column(Float, nullable=True)        # the site's chance when tracked
    ev: Mapped[float] = mapped_column(Float, nullable=True)
    actual: Mapped[float] = mapped_column(Float, nullable=True)
    result: Mapped[str] = mapped_column(String(5), nullable=True)    # win | loss | push | void; None until graded
    # the close (market_fit.attach_bet_closing): the market's fair chance of this leg at its own line, at the last odds fetch
    # before kickoff; close_books 0 = no closing quotes for him in this market. Filled once the game starts.
    close_p: Mapped[float] = mapped_column(Float, nullable=True)
    close_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    close_books: Mapped[int] = mapped_column(Integer, nullable=True)
    graded_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    lead: Mapped[float] = mapped_column(Float, nullable=True)   # a moneyline's team's largest lead in the game (early-win bets)
    # the bettor marked it voided by the book though the player played (injury protection, a leg the book pulled):
    # result is "void" and grading leaves it alone; actual keeps his stat line if grading had already run
    book_void: Mapped[bool] = mapped_column(Boolean, nullable=True)
    bet: Mapped[Bet] = relationship(back_populates="legs")


class SyncRun(Base):
    """One sync (model run and/or odds fetch), scheduled or started by hand: the admin page's run log."""
    __tablename__ = "sync_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    slot: Mapped[str] = mapped_column(String(32), index=True)      # schedule slot id, or "manual"
    trigger: Mapped[str] = mapped_column(String(12))               # schedule | catch-up | manual
    scheduled_for: Mapped[datetime] = mapped_column(DateTime, nullable=True, index=True)  # UTC slot time it ran for
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="running")  # running | ok | partial | error | skipped | interrupted
    odds_mode: Mapped[str] = mapped_column(String(8))
    full: Mapped[bool] = mapped_column(Boolean, default=False)
    season: Mapped[int] = mapped_column(Integer, nullable=True)
    week: Mapped[int] = mapped_column(Integer, nullable=True)
    games_fetched: Mapped[int] = mapped_column(Integer, default=0)
    credits_before: Mapped[int] = mapped_column(Integer, nullable=True)
    credits_after: Mapped[int] = mapped_column(Integer, nullable=True)
    summary: Mapped[str] = mapped_column(String(500), default="")
    log: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str] = mapped_column(String(1000), nullable=True)


class PickLog(Base):
    """The board as it stood before kickoff, one row per player/market/side, graded once the stats land.

    Every NFL sync re-snapshots the games still to kick off, so each row ends as the last price seen before its game
    (app/grading.py). Kept across runs and weeks: it's how the model is checked against real results and real lines."""
    __tablename__ = "pick_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    season: Mapped[int] = mapped_column(Integer)
    week: Mapped[int] = mapped_column(Integer, index=True)
    game_id: Mapped[str] = mapped_column(String(24), index=True)
    gameday: Mapped[str] = mapped_column(String(12))
    gametime: Mapped[str] = mapped_column(String(8))
    player_id: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(80))
    pos: Mapped[str] = mapped_column(String(4))
    team: Mapped[str] = mapped_column(String(4))
    kind: Mapped[str] = mapped_column(String(6))
    side: Mapped[str] = mapped_column(String(5))
    line: Mapped[float] = mapped_column(Float)
    odds: Mapped[int] = mapped_column(Integer)
    book: Mapped[str] = mapped_column(String(40))
    mu: Mapped[float] = mapped_column(Float)
    prob: Mapped[float] = mapped_column(Float)
    p_model: Mapped[float] = mapped_column(Float)
    p_mkt: Mapped[float] = mapped_column(Float)
    ev: Mapped[float] = mapped_column(Float)
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    run_id: Mapped[int] = mapped_column(Integer)
    snapped_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    actual: Mapped[float] = mapped_column(Float, nullable=True)
    result: Mapped[str] = mapped_column(String(5), nullable=True)   # win | loss | push | void (didn't play); None until graded
    graded_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    hours: Mapped[float] = mapped_column(Float, nullable=True)      # hours from the price's fetch to kickoff
    # the price as first shown on the board (EV 3%+, no flag): what a visitor could have bet earliest
    first_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    first_line: Mapped[float] = mapped_column(Float, nullable=True)
    first_odds: Mapped[int] = mapped_column(Integer, nullable=True)
    first_book: Mapped[str] = mapped_column(String(40), nullable=True)
    first_ev: Mapped[float] = mapped_column(Float, nullable=True)
    first_p_mkt: Mapped[float] = mapped_column(Float, nullable=True)
    # the close (app/market_fit.py): the market's fair chance of this side at the last fetch before kickoff, at this
    # row's line and at first_line; close_books 0 = no closing quotes for this player and market
    close_p: Mapped[float] = mapped_column(Float, nullable=True)
    close_p_first: Mapped[float] = mapped_column(Float, nullable=True)
    close_line: Mapped[float] = mapped_column(Float, nullable=True)
    close_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    close_books: Mapped[int] = mapped_column(Integer, nullable=True)


Index("ix_pick_log_key", PickLog.game_id, PickLog.player_id, PickLog.kind, PickLog.side, unique=True)


class NbaPickLog(Base):
    """PickLog for the NBA: every NBA sync re-snapshots the slate's games still to tip off, and each NBA model run
    grades finished games from ESPN's game logs (app/nba/grading.py). No week: the report groups by calendar week."""
    __tablename__ = "nba_pick_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    season: Mapped[int] = mapped_column(Integer)
    game_id: Mapped[str] = mapped_column(String(32), index=True)
    gameday: Mapped[str] = mapped_column(String(12), index=True)
    gametime: Mapped[str] = mapped_column(String(8))
    player_id: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(80))
    pos: Mapped[str] = mapped_column(String(4))
    team: Mapped[str] = mapped_column(String(5))
    kind: Mapped[str] = mapped_column(String(6))
    side: Mapped[str] = mapped_column(String(5))
    line: Mapped[float] = mapped_column(Float)
    odds: Mapped[int] = mapped_column(Integer)
    book: Mapped[str] = mapped_column(String(40))
    mu: Mapped[float] = mapped_column(Float)
    prob: Mapped[float] = mapped_column(Float)
    p_model: Mapped[float] = mapped_column(Float)
    p_mkt: Mapped[float] = mapped_column(Float)
    ev: Mapped[float] = mapped_column(Float)
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    run_id: Mapped[int] = mapped_column(Integer)
    snapped_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    actual: Mapped[float] = mapped_column(Float, nullable=True)
    result: Mapped[str] = mapped_column(String(5), nullable=True)   # win | loss | push | void (didn't play); None until graded
    graded_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    hours: Mapped[float] = mapped_column(Float, nullable=True)      # hours from the price's fetch to kickoff
    # the price as first shown on the board (EV 3%+, no flag): what a visitor could have bet earliest
    first_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    first_line: Mapped[float] = mapped_column(Float, nullable=True)
    first_odds: Mapped[int] = mapped_column(Integer, nullable=True)
    first_book: Mapped[str] = mapped_column(String(40), nullable=True)
    first_ev: Mapped[float] = mapped_column(Float, nullable=True)
    first_p_mkt: Mapped[float] = mapped_column(Float, nullable=True)
    # the close (app/market_fit.py): the market's fair chance of this side at the last fetch before kickoff, at this
    # row's line and at first_line; close_books 0 = no closing quotes for this player and market
    close_p: Mapped[float] = mapped_column(Float, nullable=True)
    close_p_first: Mapped[float] = mapped_column(Float, nullable=True)
    close_line: Mapped[float] = mapped_column(Float, nullable=True)
    close_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    close_books: Mapped[int] = mapped_column(Integer, nullable=True)


Index("ix_nba_pick_log_key", NbaPickLog.game_id, NbaPickLog.player_id, NbaPickLog.kind, NbaPickLog.side, unique=True)


class PickSnap(Base):
    """Every price the board showed, one row per sync and player/market/side, appended (a row is skipped when nothing
    changed since that key's last one). pick_log keeps only the last price before kickoff; this keeps the earlier ones
    with their model and market chances, so how much to trust the market can be fitted by how long before kickoff the
    price was posted (app/market_fit.py). NFL and NBA share it (sport); their game ids can't collide."""
    __tablename__ = "pick_snaps"
    id: Mapped[int] = mapped_column(primary_key=True)
    sport: Mapped[str] = mapped_column(String(4))
    snapped_at: Mapped[datetime] = mapped_column(DateTime)
    game_id: Mapped[str] = mapped_column(String(32))
    player_id: Mapped[str] = mapped_column(String(20))
    kind: Mapped[str] = mapped_column(String(6))
    side: Mapped[str] = mapped_column(String(5))
    line: Mapped[float] = mapped_column(Float)
    odds: Mapped[int] = mapped_column(Integer)
    book: Mapped[str] = mapped_column(String(40))
    mu: Mapped[float] = mapped_column(Float)
    p_model: Mapped[float] = mapped_column(Float)
    p_mkt: Mapped[float] = mapped_column(Float)
    ev: Mapped[float] = mapped_column(Float)
    hours: Mapped[float] = mapped_column(Float, nullable=True)
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (Index("ix_pick_snaps_key", "game_id", "player_id", "kind", "side"),)


def backfill_game_ids(eng) -> int:
    """Tag quotes stored before game_id existed with the game that was upcoming when they were fetched.

    Uses every projection run (player -> game, with the game's date): a quote fetched on a given day belongs to the
    player's first game on or after that day. So it is correct whichever run happens to be newest."""
    from sqlalchemy.orm import Session
    from .engine import odds
    with Session(eng) as s:
        legacy = s.query(OddsLine).filter(OddsLine.game_id.is_(None)).all()
        if not legacy:
            return 0
        games = {}  # normalized player name -> {gameday: game_id}
        for name, gameday, gid in s.query(Projection.name, Projection.gameday, Projection.game_id).distinct():
            games.setdefault(odds.norm(name), {})[gameday] = gid
        done = 0
        for r in legacy:
            fetched_day = r.fetched_at.date().isoformat()
            upcoming = sorted((day, gid) for day, gid in games.get(odds.norm(r.player), {}).items() if day >= fetched_day)
            if upcoming:
                r.game_id = upcoming[0][1]
                done += 1
        s.commit()
        return done


PICK_LOG_ADDED = (("hours", "FLOAT"), ("first_at", "TIMESTAMP"), ("first_line", "FLOAT"), ("first_odds", "INTEGER"),
                  ("first_book", "VARCHAR(40)"), ("first_ev", "FLOAT"), ("first_p_mkt", "FLOAT"), ("close_p", "FLOAT"),
                  ("close_p_first", "FLOAT"), ("close_line", "FLOAT"), ("close_at", "TIMESTAMP"), ("close_books", "INTEGER"))


def ensure_columns(eng) -> bool:
    """create_all never alters existing tables; add the link columns to databases created before they existed.
    Returns True when game_id was just added (so the caller can tag the existing quotes)."""
    from sqlalchemy import inspect, text
    have = {c["name"] for c in inspect(eng).get_columns("odds_lines")}
    with eng.begin() as conn:
        for col in ("over_link", "under_link", "event_link"):
            if col not in have:
                conn.execute(text(f"ALTER TABLE odds_lines ADD COLUMN {col} VARCHAR(500)"))
        added = "game_id" not in have
        if added:
            conn.execute(text("ALTER TABLE odds_lines ADD COLUMN game_id VARCHAR(24)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_odds_lines_game_id ON odds_lines (game_id)"))
    for table, col, sql_type in (("runs", "weather", "JSON"), ("runs", "teams", "JSON"), ("projections", "wx", "FLOAT"),
                                 ("projections", "teammate_out", "VARCHAR(160)"), ("projections", "thin", "BOOLEAN"),
                                 ("projections", "qb_adj", "FLOAT"), ("projections", "qb_name", "VARCHAR(80)"),
                                 ("nba_projections", "parts", "JSON"), ("users", "favorites", "JSON"),
                                 # bets.tags: a database whose bets table predates tags (an early local one)
                                 ("bets", "tags", "JSON"), ("bets", "description", "VARCHAR(200)"),
                                 ("bets", "bonus", "BOOLEAN NOT NULL DEFAULT FALSE"),
                                 ("bets", "late", "BOOLEAN NOT NULL DEFAULT FALSE"),
                                 ("bets", "boost", "INTEGER"), ("bets", "no_sweat", "FLOAT"),
                                 ("bets", "early_win", "INTEGER"), ("bets", "boost_cap", "FLOAT"), ("bet_legs", "lead", "FLOAT"), ("bet_legs", "book_void", "BOOLEAN"),
                                 ("bet_legs", "close_p", "FLOAT"), ("bet_legs", "close_at", "TIMESTAMP"), ("bet_legs", "close_books", "INTEGER"),
                                 *((t, c, typ) for t in ("projections", "nba_projections")
                                   for c, typ in (("dvp", "FLOAT"), ("dvp_rank", "INTEGER"), ("dvp_n", "INTEGER"))),
                                 *((t, c, typ) for t in ("pick_log", "nba_pick_log") for c, typ in PICK_LOG_ADDED)):
        if col not in {c["name"] for c in inspect(eng).get_columns(table)}:
            with eng.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {sql_type}"))
    return added


def _sqlite_pragmas(dbapi_conn, _record):
    """WAL lets the site keep reading while a sync writes; busy_timeout waits out a lock instead of failing."""
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")  # safe with WAL; fewer fsyncs per commit
    cur.execute("PRAGMA busy_timeout=10000")
    cur.close()


def _finite(v):
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, dict):
        return {k: _finite(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_finite(x) for x in v]
    return v


def _json_dumps(v) -> str:
    """Postgres refuses NaN and Infinity in JSON (SQLite stored them); write them as null so a sync can't fail on one."""
    return json.dumps(_finite(v))


def make_engine(url=None):
    url = url or config.DATABASE_URL
    if url.startswith("sqlite:///") and not url.endswith(":memory:"):
        os.makedirs(os.path.dirname(url.replace("sqlite:///", "")), exist_ok=True)
    kwargs = ({"connect_args": {"check_same_thread": False}} if url.startswith("sqlite")
              else {"pool_pre_ping": True, "pool_recycle": 300, "json_serializer": _json_dumps})
    if url.endswith(":memory:"):
        from sqlalchemy.pool import StaticPool
        kwargs["poolclass"] = StaticPool
    eng = create_engine(url, **kwargs)
    if url.startswith("sqlite") and not url.endswith(":memory:"):
        event.listen(eng, "connect", _sqlite_pragmas)
    Base.metadata.create_all(eng)
    if ensure_columns(eng):
        backfill_game_ids(eng)
    return eng


def _free_volume():
    """The NBA cache shares the database's volume; clear its old raw gamelogs before SQLite needs room for its WAL."""
    from .nba.espn import prune_raw_gamelogs
    n = prune_raw_gamelogs()
    if n:
        print(f"Removed {n} raw NBA gamelog files from {config.NBA_CACHE}")


_free_volume()
engine = make_engine()
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def proj_frame(session, run_id: int):
    """A run's projections as a DataFrame (the shape engine.picks expects)."""
    import pandas as pd
    from sqlalchemy import select
    rows = session.scalars(select(Projection).where(Projection.run_id == run_id).order_by(Projection.id)).all()
    return pd.DataFrame([{c.name: getattr(r, c.name) for c in Projection.__table__.columns} for r in rows])


def current_lines(session, table=None):
    """The newest quotes, as a DataFrame in the shape engine.picks expects (table: OddsLine, or NbaOddsLine).

    Newest per market AND game, so lines fetched for different games at different times (Thursday's game first,
    the Sunday slate later) all stay visible. Rows from before game_id existed are grouped per market."""
    import pandas as pd
    from sqlalchemy import func, select
    table = table or OddsLine
    # every board, ladder and parlay request reads this; it only changes on an odds sync, so it's rebuilt when the
    # table's rows do (store_lines also clears it). Kept per engine, so separate databases (tests) never share one.
    version = tuple(session.execute(select(func.count(), func.min(table.id), func.max(table.id), func.max(table.fetched_at),
                                           func.sum(table.line))).one())
    per_engine = _LINES_CACHE.setdefault(session.get_bind(), {})
    hit = per_engine.get(table.__tablename__)
    if hit and hit[0] == version:
        return hit[1]
    # Plain column tuples, not ORM objects: the NBA table is 150k+ rows, and loading them as objects peaked at ~430 MB
    # of the web process's memory, most of which it never handed back.
    cols = ["player", "market", "line", "over_odds", "under_odds", "book", "over_link", "under_link", "event_link",
            "game_id", "fetched_at"]
    rows = session.execute(select(*(getattr(table, c) for c in cols)).order_by(table.id)).all()   # id order, as SQLite gave it: ties break the same way on Postgres
    newest = {}
    for r in rows:
        k = (r.market, r.game_id)
        if k not in newest or r.fetched_at > newest[k]:
            newest[k] = r.fetched_at
    # One copy of each repeated string (player, market, book, game, event link) instead of one per row: the cached
    # frame lives as long as the process, and this roughly halves it. Same values and dtypes either way.
    canon = {}
    one = lambda v: canon.setdefault(v, v) if isinstance(v, str) else v
    keep = [tuple(map(one, r)) for r in rows if r.fetched_at == newest[(r.market, r.game_id)]]
    del rows
    df = pd.DataFrame.from_records(keep, columns=cols)
    per_engine[table.__tablename__] = (version, df)
    return df   # shared between requests: callers filter or copy it, never change it in place


def player_quotes(session, market: str, key: str, game_id, table=None):
    """current_lines' rows for one player (key: odds.norm of the name) in one market and game, in the same order and
    with the same index as filtering the whole frame. A board visit asks for ~50 players' ladders at once; each used to
    normalize and mask every line ever fetched (150k+ NBA rows), which took seconds and churned the heap. The lookup is
    built once per version of the lines."""
    table = table or OddsLine
    lines = current_lines(session, table)
    per_engine = _LINES_CACHE[session.get_bind()]
    slot = table.__tablename__ + ":by_player"
    hit = per_engine.get(slot)
    if not hit or hit[0]() is not lines:
        with _INDEX_LOCK:   # the ~50 requests arrive together: build it once, not once each
            hit = per_engine.get(slot)
            if not hit or hit[0]() is not lines:
                from .engine import odds
                groups = lines.groupby([lines.market, lines.player.map(odds.norm), lines.game_id], sort=False).indices
                hit = per_engine[slot] = (weakref.ref(lines), groups)   # weak: never keeps a replaced frame alive
    rows = hit[1].get((market, key, game_id))
    return lines.iloc[rows if rows is not None else []]


_LINES_CACHE = weakref.WeakKeyDictionary()   # engine -> {table name: (version, current lines)}
_INDEX_LOCK = threading.Lock()


def clear_lines_cache():
    _LINES_CACHE.clear()
