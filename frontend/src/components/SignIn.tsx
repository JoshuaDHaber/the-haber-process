import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, useSyncExternalStore, type FormEvent } from "react";
import { confirmAge, emailSignIn, googleHref, herePath, signOut, useAuth, verifyCode } from "../auth";
import { BASE } from "../env";
import Sheet from "./Sheet";

// The sign-in sheet opens from anywhere (the top bar, the preferences page, the admin page), so whether it's open lives here.
let sheet: { error?: string; why?: string } | null = null;
const listeners = new Set<() => void>();
const notify = () => listeners.forEach((l) => l());
/** why: what signing in gets them where they opened it ("Track your bets…"), in place of the general line. */
export const openSignIn = (error?: string, why?: string) => { sheet = { error, why }; notify(); };
const closeSignIn = () => { sheet = null; notify(); };
const useSheet = () => useSyncExternalStore((cb) => (listeners.add(cb), () => listeners.delete(cb)), () => sheet);

/** ?signin=… on a page Google sent back to: what went wrong. */
const FAILED: Record<string, string> = {
  failed: "Google sign-in didn't finish. Try again, or use your email.",
  unverified: "Google hasn't verified that account's email, so it can't sign in here. Use your email instead.",
};

const GoogleG = () => (
  <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true">
    <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
    <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
    <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
    <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
  </svg>
);

/** After a sign-in in place (a code typed here): what the page shows for admins (Admin, can_write) changes with it. */
function useRefetchAccountData() {
  const qc = useQueryClient();
  return () => { qc.invalidateQueries({ queryKey: ["meta"] }); qc.invalidateQueries({ queryKey: ["admin"] }); };
}

function SignInSheet({ error, why }: { error?: string; why?: string }) {
  const auth = useAuth();
  const refetch = useRefetchAccountData();
  const [email, setEmail] = useState("");
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState(error ?? "");
  const [wait, setWait] = useState(0);
  useEffect(() => { if (wait > 0) { const t = setTimeout(() => setWait(wait - 1), 1000); return () => clearTimeout(t); } }, [wait]);
  const next = herePath();

  const send = async (e?: FormEvent) => {
    e?.preventDefault();
    const to = (sentTo ?? email).trim();
    if (!to) return;
    setBusy(true); setProblem("");
    try { await emailSignIn(to, next); setSentTo(to); setCode(""); setWait(30); }
    catch (err) { setProblem((err as Error).message); }
    finally { setBusy(false); }
  };
  const verify = async (e?: FormEvent) => {
    e?.preventDefault();
    if (code.length !== 6 || !sentTo) return;
    setBusy(true); setProblem("");
    try { await verifyCode(sentTo, code); refetch(); closeSignIn(); }
    catch (err) { setProblem((err as Error).message); }
    finally { setBusy(false); }
  };

  return (
    <Sheet label="Sign in" overlayClass="signin-overlay" onClose={closeSignIn}>
      {sentTo ? <>
        <h2>Check your email</h2>
        <p className="sub auth-lede">We sent a sign-in link and a 6-digit code to <b>{sentTo}</b>. Open the link on this device, or enter the code here.</p>
        {problem && <p className="err auth-problem" role="alert">{problem}</p>}
        <form className="auth-form" onSubmit={verify}>
          <label className="auth-label" htmlFor="auth-code">Code</label>
          <input id="auth-code" className="auth-input auth-code" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]*" maxLength={6} autoFocus
            value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))} placeholder="123456" />
          <button className="primary" disabled={busy || code.length !== 6}>{busy ? "Signing in…" : "Sign in"}</button>
        </form>
        <p className="auth-links">
          <button className="link" disabled={busy || wait > 0} onClick={() => send()}>{wait > 0 ? `Send a new code in ${wait}s` : "Send a new code"}</button>
          <span className="mut">·</span>
          <button className="link" onClick={() => { setSentTo(null); setProblem(""); }}>Use a different email</button>
        </p>
      </> : <>
        <h2>Sign in</h2>
        <p className="sub auth-lede">{why ?? "Keep your preferences on every device."} Every board stays free to browse without an account.</p>
        {problem && <p className="err auth-problem" role="alert">{problem}</p>}
        {auth?.google && <>
          <a className="auth-google" href={googleHref(next)}><GoogleG />Continue with Google</a>
          <div className="auth-or"><span>or</span></div>
        </>}
        <form className="auth-form" onSubmit={send}>
          <label className="auth-label" htmlFor="auth-email">Email</label>
          <input id="auth-email" className="auth-input" type="email" autoComplete="email" required autoFocus={!auth?.google}
            value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" />
          <button className="primary" disabled={busy || !email.trim()}>{busy ? "Sending…" : "Email me a sign-in link"}</button>
        </form>
        <p className="mut auth-fine">No password needed. Accounts are for people 21 and older. <a href={`${BASE}privacy`} target="_blank" rel="noopener noreferrer">Privacy policy</a></p>
      </>}
    </Sheet>
  );
}

/** A new account says it's 21 or older once, before anything is saved to it. */
function AgeGate({ onDismiss }: { onDismiss: () => void }) {
  const refetch = useRefetchAccountData();
  const [ok, setOk] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const go = async () => {
    setBusy(true); setProblem("");
    try { await confirmAge(); refetch(); } catch (err) { setProblem((err as Error).message); setBusy(false); }
  };
  return (
    <Sheet label="Confirm your age" overlayClass="signin-overlay" onClose={onDismiss}>
      <h2>One more step</h2>
      <p className="sub auth-lede">The Haber Process is about sports betting, so accounts are for people 21 and older.</p>
      {problem && <p className="err auth-problem" role="alert">{problem}</p>}
      <label className="admin-toggle auth-age">
        <input type="checkbox" checked={ok} onChange={(e) => setOk(e.target.checked)} />
        <span>I'm 21 or older</span>
      </label>
      <div className="auth-form">
        <button className="primary" disabled={!ok || busy} onClick={go}>{busy ? "Saving…" : "Continue"}</button>
      </div>
      <p className="auth-links"><button className="link" onClick={async () => { await signOut().catch(() => {}); refetch(); onDismiss(); }}>Sign out instead</button></p>
      <p className="mut auth-fine">Gambling problem? Call or text <a href="tel:+18004262537">1-800-GAMBLER</a>.</p>
    </Sheet>
  );
}

/** Once per page: the sign-in sheet when something opens it, the age check for a new account, and Google's error, if any. */
export default function AuthLayer() {
  const open = useSheet();
  const auth = useAuth();
  const [ageDismissed, setAgeDismissed] = useState(false);
  useEffect(() => {
    const qs = new URLSearchParams(window.location.search);
    const why = qs.get("signin");
    if (!why) return;
    qs.delete("signin");
    window.history.replaceState(null, "", window.location.pathname + (qs.size ? `?${qs}` : "") + window.location.hash);
    openSignIn(FAILED[why] ?? FAILED.failed);
  }, []);
  if (open) return <SignInSheet key="signin" error={open.error} why={open.why} />;
  if (auth?.user && !auth.user.age_confirmed && !ageDismissed) return <AgeGate onDismiss={() => setAgeDismissed(true)} />;
  return null;
}
