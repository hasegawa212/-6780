"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, api, type Me } from "./api";

/** ログイン中のユーザー。未ログイン（401）ならログイン画面へ */
export function useMe(): Me | undefined {
  const router = useRouter();
  const [me, setMe] = useState<Me>();
  useEffect(() => {
    api<Me>("GET", "/v1/me")
      .then((r) => setMe(r.data))
      .catch((e: unknown) => {
        if (e instanceof ApiError && e.status === 401) router.replace("/login");
      });
  }, [router]);
  return me;
}
