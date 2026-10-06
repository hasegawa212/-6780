"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { api, forgetCsrf, type Me } from "../lib/api";

export function AppHeader({ me }: { me: Me | undefined }) {
  const router = useRouter();
  const logout = async () => {
    await api("POST", "/v1/auth/logout").catch(() => undefined);
    forgetCsrf();
    router.replace("/login");
  };
  return (
    <header className="flex items-center justify-between border-b border-border px-4 py-3">
      <Link href="/leads" className="font-bold text-accent">
        TAC
      </Link>
      <div className="flex items-center gap-4">
        <span className="text-text-secondary">{me ? `${me.displayName}（${me.role}）` : ""}</span>
        <button
          type="button"
          onClick={logout}
          className="min-h-11 rounded-lg border border-border-strong px-3"
        >
          ログアウト
        </button>
      </div>
    </header>
  );
}
