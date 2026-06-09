"use client";

import { useEffect, useState } from "react";

const backendUrl = process.env.NEXT_PUBLIC_BACKEND_URL;

async function request(path: string, token: string, method: string = "GET", body?: unknown) {
  if (!backendUrl) throw new Error("Brak NEXT_PUBLIC_BACKEND_URL");
  const response = await fetch(`${backendUrl}/api${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || "API error");
  return data;
}

export default function AdminPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [otp, setOtp] = useState("");
  const [token, setToken] = useState("");
  const [dashboard, setDashboard] = useState<any>(null);
  const [users, setUsers] = useState<any[]>([]);
  const [listings, setListings] = useState<any[]>([]);
  const [disputes, setDisputes] = useState<any[]>([]);
  const [reports, setReports] = useState<any[]>([]);
  const [wallets, setWallets] = useState<any[]>([]);
  const [auditLogs, setAuditLogs] = useState<any[]>([]);
  const [error, setError] = useState("");

  const loadAll = async (accessToken: string) => {
    const [d, u, l, di, rp, wa, al] = await Promise.all([
      request("/admin/dashboard", accessToken),
      request("/admin/users", accessToken),
      request("/admin/listings", accessToken),
      request("/admin/disputes", accessToken),
      request("/admin/reports", accessToken),
      request("/admin/platform-wallets", accessToken),
      request("/admin/audit-logs", accessToken),
    ]);
    setDashboard(d);
    setUsers(u);
    setListings(l);
    setDisputes(di);
    setReports(rp);
    setWallets(wa);
    setAuditLogs(al);
  };

  const login = async () => {
    try {
      setError("");
      if (!backendUrl) {
        throw new Error("Brak NEXT_PUBLIC_BACKEND_URL");
      }
      const response = await fetch(`${backendUrl}/api/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password, otp_code: otp || undefined, device_name: "admin-web" }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Błąd logowania");
      setToken(data.access_token);
      await loadAll(data.access_token);
    } catch (e: any) {
      setError(e.message || "Błąd logowania");
    }
  };

  useEffect(() => {
    if (token) loadAll(token).catch(() => null);
  }, [token]);

  if (!token) {
    return (
      <main className="wrap">
        <h1 className="title">MASK Admin</h1>
        <p className="subtitle">Panel administracyjny: moderacja, escrow, spory, audyt.</p>
        <div className="panel grid" style={{ maxWidth: 420 }}>
          <input className="input" placeholder="E-mail" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="off" />
          <input className="input" placeholder="Hasło" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
          <input className="input" placeholder="Kod 2FA (jeśli aktywny)" value={otp} onChange={(e) => setOtp(e.target.value)} />
          <button className="btn" onClick={login}>Zaloguj jako admin</button>
          {error ? <p style={{ color: "#ff5d79" }}>{error}</p> : null}
        </div>
      </main>
    );
  }

  return (
    <main className="wrap grid" style={{ gap: 16 }}>
      <div>
        <h1 className="title">MASK Admin Dashboard</h1>
        <p className="subtitle">Pełna kontrola bezpieczeństwa i operacji marketplace.</p>
      </div>

      <section className="grid grid-4">
        <div className="panel"><p className="muted">Użytkownicy</p><h2>{dashboard?.users || 0}</h2></div>
        <div className="panel"><p className="muted">Oferty</p><h2>{dashboard?.listings || 0}</h2></div>
        <div className="panel"><p className="muted">Aktywne transakcje</p><h2>{dashboard?.active_transactions || 0}</h2></div>
        <div className="panel"><p className="muted">Otwarte spory</p><h2>{dashboard?.open_disputes || 0}</h2></div>
      </section>

      <section className="grid grid-2">
        <div className="panel grid" style={{ gap: 10 }}>
          <div className="row"><h3>Moderacja ofert</h3><button className="btn" onClick={() => loadAll(token)}>Odśwież</button></div>
          {listings.slice(0, 8).map((listing) => (
            <div key={listing.id} className="row" style={{ borderBottom: "1px solid var(--border)", paddingBottom: 8 }}>
              <div>
                <strong>{listing.title}</strong>
                <p className="muted" style={{ margin: 0 }}>{listing.status} / {listing.moderation_status}</p>
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <button
                  className="btn"
                  onClick={async () => {
                    await request(`/admin/listings/${listing.id}/moderate`, token, "POST", {
                      action: "approve",
                      reason: "Ręczne zatwierdzenie",
                    });
                    await loadAll(token);
                  }}
                >
                  Approve
                </button>
                <button
                  className="btn btn-danger"
                  onClick={async () => {
                    await request(`/admin/listings/${listing.id}/moderate`, token, "POST", {
                      action: "reject",
                      reason: "Narusza zasady",
                    });
                    await loadAll(token);
                  }}
                >
                  Reject
                </button>
              </div>
            </div>
          ))}
        </div>

        <div className="panel grid" style={{ gap: 10 }}>
          <h3>Spory i decyzje escrow</h3>
          {disputes.slice(0, 8).map((dispute) => (
            <div key={dispute.id} className="row" style={{ borderBottom: "1px solid var(--border)", paddingBottom: 8 }}>
              <div>
                <strong>{dispute.reason}</strong>
                <p className="muted" style={{ margin: 0 }}>status: {dispute.status}</p>
              </div>
              {dispute.status === "OPEN" ? (
                <button
                  className="btn"
                  onClick={async () => {
                    await request(`/admin/disputes/${dispute.id}/resolve`, token, "POST", {
                      decision: "refund_buyer",
                      reason: "Dowody po stronie kupującego",
                    });
                    await loadAll(token);
                  }}
                >
                  Refund buyer
                </button>
              ) : (
                <span className="muted">resolved</span>
              )}
            </div>
          ))}
        </div>
      </section>

      <section className="grid grid-2">
        <div className="panel grid" style={{ gap: 10 }}>
          <h3>Wallet Risk & Platform Wallets</h3>
          {wallets.map((wallet) => (
            <div key={wallet.id} className="row" style={{ borderBottom: "1px solid var(--border)", paddingBottom: 8 }}>
              <div>
                <strong>{wallet.network} / {wallet.token}</strong>
                <p className="muted" style={{ margin: 0 }}>{wallet.wallet_address}</p>
              </div>
              <span style={{ color: wallet.risk_score > 70 ? "var(--danger)" : "var(--good)" }}>Risk {wallet.risk_score || 0}</span>
            </div>
          ))}
        </div>

        <div className="panel grid" style={{ gap: 10 }}>
          <h3>Audit log (ostatnie)</h3>
          {auditLogs.slice(0, 10).map((log) => (
            <div key={log.id} style={{ borderBottom: "1px solid var(--border)", paddingBottom: 8 }}>
              <strong>{log.action}</strong>
              <p className="muted" style={{ margin: 0 }}>{log.target_type} • {log.target_id}</p>
              <p className="muted" style={{ margin: 0 }}>{new Date(log.created_at).toLocaleString()}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="panel">
        <h3>Użytkownicy (sample)</h3>
        <div className="grid" style={{ gap: 8 }}>
          {users.slice(0, 8).map((u) => (
            <div key={u.id} className="row" style={{ borderBottom: "1px solid var(--border)", paddingBottom: 8 }}>
              <div>
                <strong>{u.display_alias}</strong>
                <p className="muted" style={{ margin: 0 }}>{u.email}</p>
              </div>
              <span className="muted">risk: {u.risk_score}</span>
            </div>
          ))}
        </div>
      </section>

      <section className="panel">
        <h3>Zgłoszenia (sample)</h3>
        <div className="grid" style={{ gap: 8 }}>
          {reports.slice(0, 8).map((r) => (
            <div key={r.id} className="row" style={{ borderBottom: "1px solid var(--border)", paddingBottom: 8 }}>
              <div>
                <strong>{r.target_type}</strong>
                <p className="muted" style={{ margin: 0 }}>{r.reason}</p>
              </div>
              <span className="muted">{r.status}</span>
            </div>
          ))}
        </div>
      </section>
    </main>
  );
}