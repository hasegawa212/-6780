"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { AppHeader } from "../../components/app-header";
import { api, type ContactView } from "../../lib/api";
import { useMe } from "../../lib/use-me";

/** リード（最小）。検索・絞り込み・保存ビューは Phase 4 */
export default function LeadsPage() {
  const me = useMe();
  const [items, setItems] = useState<ContactView[]>();
  const [cursor, setCursor] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  const load = useCallback(async (after: string | null) => {
    try {
      const q = after ? `?limit=50&cursor=${encodeURIComponent(after)}` : "?limit=50";
      const r = await api<{ items: ContactView[]; nextCursor: string | null }>(
        "GET",
        `/v1/contacts${q}`,
      );
      setItems((prev) => [...(after ? (prev ?? []) : []), ...r.data.items]);
      setCursor(r.data.nextCursor);
    } catch {
      setFailed(true);
    }
  }, []);

  useEffect(() => {
    if (me) void load(null);
  }, [me, load]);

  return (
    <>
      <AppHeader me={me} />
      <main className="mx-auto max-w-4xl px-4 py-6">
        <h1 className="mb-4 text-2xl font-bold">リード</h1>
        {failed ? (
          <p role="alert" className="text-danger">
            リードを読み込めませんでした。再読み込みしてください。
          </p>
        ) : null}
        {items === undefined && !failed ? (
          <p className="text-text-secondary">読み込んでいます…</p>
        ) : null}
        {items?.length === 0 ? <p>リードはまだありません。</p> : null}
        {items && items.length > 0 ? (
          <table className="w-full border-collapse">
            <thead>
              <tr className="border-b border-border text-left text-text-secondary">
                <th scope="col" className="py-2">
                  名前
                </th>
                <th scope="col" className="py-2">
                  電話
                </th>
                <th scope="col" className="py-2">
                  <span className="sr-only">操作</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((c) => (
                <tr key={c.id} className="h-11 border-b border-border">
                  <td>{c.displayName}</td>
                  <td className="tabular-nums">{c.phone}</td>
                  <td className="text-right">
                    <Link href={`/contacts/${c.id}`} className="font-bold text-accent underline">
                      開く<span className="sr-only">：{c.displayName}</span>
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
        {cursor ? (
          <button
            type="button"
            onClick={() => load(cursor)}
            className="mt-4 min-h-11 rounded-lg border border-border-strong px-4"
          >
            さらに読み込む
          </button>
        ) : null}
      </main>
    </>
  );
}
