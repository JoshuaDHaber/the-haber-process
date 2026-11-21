import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_env(path=os.path.join(BASE_DIR, ".env")):
    """Load KEY=VALUE lines from backend/.env without overriding real environment variables."""
    if os.path.exists(path):
        for line in open(path):
            k, _, v = line.strip().partition("=")
            if k and not k.startswith("#") and v:
                os.environ.setdefault(k, v.strip("\"'"))


if not os.environ.get("NFLPROPS_NO_DOTENV"):  # tests must never pick up the real API key
    load_env()

def normalize_db_url(url: str) -> str:
    """Hosted Postgres gives postgres:// or postgresql:// URLs; SQLAlchemy needs the driver we install (psycopg 3)."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


DATABASE_URL = normalize_db_url(os.environ.get("DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'data', 'app.db')}"))
CORS_ORIGINS = lambda: [o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()]
# the weekly model run needs ~400 MB; small hosts set this to 0 and run it elsewhere against the same database
PROJECTIONS_ON_SERVER = lambda: os.environ.get("PROJECTIONS_ON_SERVER", "1") != "0"
ODDS_API_KEY = lambda: os.environ.get("ODDS_API_KEY")  # read lazily so tests can set it
# the NBA side has its own Odds API key, so its daily syncs never eat into the NFL key's credits
ODDS_API_KEY_NBA = lambda: os.environ.get("ODDS_API_KEY_NBA")
# spares: a sync switches to these once the key above reaches the credit floor (app/odds_keys.py)
ODDS_API_KEY_NFL_SECONDARY = lambda: os.environ.get("ODDS_API_KEY_NFL_SECONDARY")
ODDS_API_KEY_NBA_SECONDARY = lambda: os.environ.get("ODDS_API_KEY_NBA_SECONDARY")


def odds_keys(league: str = "nfl") -> list[tuple[str, str]]:
    """[(env var, key)] for a league's Odds API keys that are set, the one to spend first first."""
    names = ("ODDS_API_KEY_NBA", "ODDS_API_KEY_NBA_SECONDARY") if league == "nba" else ("ODDS_API_KEY", "ODDS_API_KEY_NFL_SECONDARY")
    return [(n, k) for n in names if (k := globals()[n]())]


# ESPN responses for the NBA model (game logs, rosters, schedule); next to the database so they survive redeploys
NBA_CACHE = os.environ.get("NBA_CACHE") or os.path.join(BASE_DIR, "data", "nba_cache")
# Scheduled syncs (app/scheduler.py): on by default on Railway, off elsewhere so a laptop never spends credits by itself
AUTO_SYNC = lambda: os.environ.get("AUTO_SYNC", "1" if os.environ.get("RAILWAY_ENVIRONMENT_NAME") else "0") == "1"
# sync jobs run in a child process that frees their memory on exit (app/scheduler.py); tests run them in-process
SYNC_SUBPROCESS = lambda: os.environ.get("SYNC_SUBPROCESS", "1") != "0"
# odds fetches stop before a key drops under this many credits; a sync then switches to the spare key, and the admin page says which key to rotate
ODDS_CREDIT_FLOOR = lambda: int(os.environ.get("ODDS_CREDIT_FLOOR", "10"))
# Accounts (app/auth.py). PUBLIC_URL builds emailed links and Google's redirect; the Host header is never trusted for them.
PUBLIC_URL = lambda: os.environ.get("PUBLIC_URL", "http://localhost:8000").rstrip("/")
GOOGLE_CLIENT_ID = lambda: os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = lambda: os.environ.get("GOOGLE_CLIENT_SECRET")
# unset: the sign-in link and code are printed to the server log instead of emailed (local development)
RESEND_API_KEY = lambda: os.environ.get("RESEND_API_KEY")
EMAIL_FROM = lambda: os.environ.get("EMAIL_FROM", "The Haber Process <signin@jdhaber.com>")
# signed-in, 21+ accounts with these emails get the admin page (the only way in from outside the host)
# reading bet slip screenshots on My bets (app/bet_screens.py); unset, the upload button doesn't show
ANTHROPIC_API_KEY = lambda: os.environ.get("ANTHROPIC_API_KEY")
ADMIN_EMAILS = lambda: {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}
# a request from this machine is admin (the way back in on the host). LOCAL_IS_ADMIN=0 turns that off, so a laptop shows
# the site as a visitor or a signed-in member sees it (testing with app/dev_users.py's accounts)
LOCAL_IS_ADMIN = lambda: os.environ.get("LOCAL_IS_ADMIN", "1") != "0"
FRONTEND_DIST =os.path.join(os.path.dirname(BASE_DIR), "frontend", "dist")
