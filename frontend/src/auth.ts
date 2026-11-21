import { useSyncExternalStore } from "react";
import type { PlayerHit } from "./api";
import type { League } from "./league";
import { changedPrefs, setPrefs, setPrefsSink, type Prefs } from "./prefs";

// The admin password is retired (the owner's account opens the admin page); a browser that saved one forgets it.
try { localStorage.removeItem("nfl-props-admin-token"); } catch { /* storage blocked: nothing saved */ }

// ---------- accounts: Google or an emailed link/code; the session is an httpOnly cookie the page never sees ----------
export interface Account {
  email: string;
  name: string | null;
  avatar_url: string | null;
  is_admin: boolean;
  age_confirmed: boolean;
  prefs: { [K in keyof Prefs]: Prefs[K] | null };
  /** Per league (abbreviations overlap: NFL DET isn't NBA DET). Older servers leave it out. */
  favorites?: Record<League, LeagueFavorites>;
}
export type FavPlayer = Omit<PlayerHit, "projected">;
export interface LeagueFavorites { teams: string[]; players: FavPlayer[] }
export interface AuthState { user: Account | null; google: boolean; next?: string | null }

/** null until /api/auth/me answers (the site works the same meanwhile: signing in is optional). */
let auth: AuthState | null = null;
const authListeners = new Set<() => void>();
let loading: Promise<void> | null = null;

async function call(path: string, init: RequestInit = {}): Promise<AuthState> {
  const res = await fetch(`/api/auth/${path}`, {
    ...init, credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Requested-With": "fetch", ...(init.headers as Record<string, string>) },
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Something went wrong. Try again.");
  return body as AuthState;
}

/** Set while this browser is signed in, so the next visit knows to wait for the account's settings (its sportsbook
 *  prices the board) before the first board request, rather than fetch the board twice. Visitors never wait. */
const SIGNED_IN = "signed-in";
const signedInHere = () => { try { return localStorage.getItem(SIGNED_IN) === "1"; } catch { return false; } };

/** Take a fresh answer from the server: the account's saved settings win over this browser's. */
function apply(next: AuthState, { fromSignIn = false } = {}) {
  const was = auth?.user?.email;
  auth = next;
  const user = next.user;
  try { if (user) localStorage.setItem(SIGNED_IN, "1"); else localStorage.removeItem(SIGNED_IN); } catch { /* storage blocked */ }
  if (user) {
    const saved = Object.fromEntries(Object.entries(user.prefs).filter(([, v]) => v != null)) as Partial<Prefs>;
    if (Object.keys(saved).length) setPrefs(saved, { save: false });
    // a new account starts from what this browser had already chosen
    else if (fromSignIn && user.age_confirmed && Object.keys(changedPrefs()).length) void savePrefs(changedPrefs());
  }
  authListeners.forEach((l) => l());
  return was !== user?.email;
}

export function loadAuth(): Promise<void> {
  loading ??= call("me").then((s) => { apply(s); }, () => { auth = { user: null, google: false }; authListeners.forEach((l) => l()); });
  return loading;
}

/** Whether the account's settings are in (or there's no account here to wait for): queries priced at the visitor's
 *  sportsbook wait for this, so a signed-in visit asks for its own book's board the first time. */
export function useSettingsReady(): boolean {
  const s = useAuth();
  return s !== null || !signedInHere();
}

export function useAuth(): AuthState | null {
  const s = useSyncExternalStore((cb) => (authListeners.add(cb), () => authListeners.delete(cb)), () => auth);
  if (!s && !loading) void loadAuth();
  return s;
}

/** Saving a change made while signed in (and 21+ confirmed); a failure leaves this browser's copy as it is. */
async function savePrefs(p: Partial<Prefs>) {
  if (!auth?.user?.age_confirmed) return;
  try { apply(await call("prefs", { method: "PUT", body: JSON.stringify(p) })); } catch { /* kept in this browser */ }
}
setPrefsSink((p) => { void savePrefs(p); });

/** Replace one league's favorite teams or players. Shown at once; put back as the account has them if the save fails. */
export async function saveFavorites(league: League, change: Partial<LeagueFavorites>) {
  const user = auth?.user;
  if (!user) return;
  const was = user.favorites;
  const mine = was?.[league] ?? { teams: [], players: [] };
  const optimistic = (f: Account["favorites"]) => { auth = { ...auth!, user: { ...user, favorites: f } }; authListeners.forEach((l) => l()); };
  optimistic({ nfl: { teams: [], players: [] }, nba: { teams: [], players: [] }, ...was, [league]: { ...mine, ...change } });
  try { apply(await call("favorites", { method: "PUT", body: JSON.stringify({ league, ...change }) })); }
  catch (err) { optimistic(was); throw err; }
}

/** Each returns whether who's signed in changed, so callers can refetch what depends on it (the admin page, can_write). */
export const emailSignIn = (email: string, next: string) => call("email", { method: "POST", body: JSON.stringify({ email, next }) });
export const verifyLink = async (token: string) => { const s = await call("email/verify", { method: "POST", body: JSON.stringify({ token }) }); apply(s, { fromSignIn: true }); return s; };
export const verifyCode = async (email: string, code: string) => { const s = await call("email/verify", { method: "POST", body: JSON.stringify({ email, code }) }); apply(s, { fromSignIn: true }); return s; };
export const confirmAge = async () => { apply(await call("age", { method: "POST" }), { fromSignIn: true }); };
export const signOut = async () => apply(await call("logout", { method: "POST" }));
export const deleteAccount = async () => apply(await call("me", { method: "DELETE" }));
export const googleHref = (next: string) => `/api/auth/google/start?next=${encodeURIComponent(next)}`;
/** Where you are now, to come back to after signing in. */
export const herePath = () => window.location.pathname + window.location.search + window.location.hash;
