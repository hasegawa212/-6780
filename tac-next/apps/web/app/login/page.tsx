"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";
import { ApiError, api, rememberCsrf } from "../../lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    setBusy(true);
    setError(undefined);
    try {
      const r = await api<{ csrfToken: string }>("POST", "/v1/auth/login", {
        body: { email: String(form.get("email")), password: String(form.get("password")) },
      });
      rememberCsrf(r.data.csrfToken);
      router.push("/leads");
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 401
          ? "メールアドレスまたはパスワードが正しくありません"
          : "ログインできませんでした。時間をおいてもう一度お試しください",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="mx-auto mt-24 max-w-sm px-4">
      <h1 className="mb-6 text-2xl font-bold">ログイン</h1>
      <form onSubmit={submit} className="space-y-4">
        <div>
          <label htmlFor="email" className="block font-bold">
            メールアドレス
          </label>
          <input
            id="email"
            name="email"
            type="email"
            autoComplete="username"
            required
            className="min-h-11 w-full rounded border border-border-strong bg-surface px-3"
          />
        </div>
        <div>
          <label htmlFor="password" className="block font-bold">
            パスワード
          </label>
          <input
            id="password"
            name="password"
            type="password"
            autoComplete="current-password"
            required
            className="min-h-11 w-full rounded border border-border-strong bg-surface px-3"
          />
        </div>
        {error ? (
          <p role="alert" className="text-danger">
            {error}
          </p>
        ) : null}
        <button
          type="submit"
          disabled={busy}
          className="min-h-11 w-full rounded-lg bg-accent font-bold text-white disabled:opacity-50"
        >
          {busy ? "ログインしています…" : "ログイン"}
        </button>
      </form>
    </main>
  );
}
