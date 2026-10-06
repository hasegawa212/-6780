"use client";

import { isTerminalCall } from "@tac/domain";
import {
  blockedReasonLabel,
  CallStarter,
  callIndicator,
  callStartSettlement,
  canOfferCall,
  conversationForMode,
  errorCopy,
  errorKindForCallError,
  outcomeButtons,
  type SuppressionUiState,
  suppressionBanner,
  suppressionFromApi,
} from "@tac/workspace";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { AppHeader } from "../../../components/app-header";
import { ErrorNotice, SuppressionBannerView } from "../../../components/notice";
import { OutcomeBar, type OutcomeSubmission } from "../../../components/outcome-bar";
import { StatusBadge } from "../../../components/status-badge";
import { ApiError, api, type CallView, type ContactView } from "../../../lib/api";
import { useMe } from "../../../lib/use-me";

interface Campaign {
  id: string;
  product: string;
  paused: boolean;
}

/**
 * Call Workspace（最初の版）：相手 → 発信 → 状態 → 結果。
 * 表示の判断は packages/workspace、発信の可否はサーバー。楽観的 UI にしない。
 * 文字起こし・AI の状態・引き継ぎは Phase 12・13（人が掛ける通話だけ）。
 */
export default function CallWorkspacePage() {
  const me = useMe();
  const { id: contactId = "" } = useParams<{ id: string }>();
  const [contact, setContact] = useState<ContactView>();
  const [suppression, setSuppression] = useState<SuppressionUiState>("UNKNOWN");
  const [campaign, setCampaign] = useState<Campaign>();
  const [call, setCall] = useState<CallView>();
  const [placing, setPlacing] = useState(false);
  const [callError, setCallError] = useState<ApiError>();
  const [recorded, setRecorded] = useState<string>();
  const [loadFailed, setLoadFailed] = useState(false);
  const starter = useRef(new CallStarter(() => crypto.randomUUID()));

  const loadContact = useCallback(async () => {
    try {
      const r = await api<{ contact: ContactView; suppression: string }>(
        "GET",
        `/v1/contacts/${contactId}`,
      );
      setContact(r.data.contact);
      setSuppression(suppressionFromApi(r.data.suppression));
    } catch {
      setSuppression("UNKNOWN");
      setLoadFailed(true);
    }
  }, [contactId]);

  useEffect(() => {
    if (!me) return;
    void loadContact();
    api<{ items: Campaign[] }>("GET", "/v1/campaigns")
      .then((r) => setCampaign(r.data.items.find((c) => !c.paused)))
      .catch(() => setLoadFailed(true));
  }, [me, loadContact]);

  // 通話が生きている間は状態を取りに行く（SSE は後続。今は 1 秒ごと）
  useEffect(() => {
    if (!call || isTerminalCall(call.status)) return;
    const timer = setInterval(() => {
      api<{ call: CallView }>("GET", `/v1/calls/${call.id}`)
        .then((r) => setCall(r.data.call))
        .catch(() => undefined);
    }, 1000);
    return () => clearInterval(timer);
  }, [call]);

  const live = call !== undefined && !isTerminalCall(call.status);
  const offerCall = canOfferCall({ suppression, activeCall: live }) && campaign !== undefined;

  const placeCall = async () => {
    if (!campaign) return;
    const { key, duplicate } = starter.current.request(contactId);
    if (duplicate) return; // 応答待ちの連打は送らない
    setPlacing(true);
    setCallError(undefined);
    setRecorded(undefined);
    try {
      const r = await api<{ call: CallView }>("POST", "/v1/calls", {
        headers: { "idempotency-key": key },
        body: { contactId, campaignId: campaign.id, mode: "HUMAN_DIALED" },
      });
      starter.current.settle(contactId, "CONFIRMED");
      setCall(r.data.call);
    } catch (e) {
      const error = e instanceof ApiError ? e : new ApiError(undefined, undefined);
      starter.current.settle(contactId, callStartSettlement(error.status));
      setCallError(error);
      if (error.code === "CONTACT_SUPPRESSED") setSuppression("SAVED");
    } finally {
      setPlacing(false);
    }
  };

  const recordOutcome = async (s: OutcomeSubmission) => {
    if (!call) return;
    const r = await api<{ suppressed: boolean }>("POST", `/v1/calls/${call.id}/outcome`, {
      body: {
        outcome: s.code,
        ...(s.at && s.code === "APPOINTMENT_SET" ? { appointmentAt: s.at } : {}),
        ...(s.at && s.code === "CALLBACK_REQUESTED" ? { callbackAt: s.at } : {}),
      },
    });
    const label = outcomeButtons().find((b) => b.code === s.code)?.label ?? s.code;
    setRecorded(`『${label}』で記録しました`);
    if (r.data.suppressed) await loadContact();
  };

  const banner = suppressionBanner(suppression);

  return (
    <>
      <AppHeader me={me} />
      <main className="mx-auto max-w-4xl space-y-6 px-4 py-6">
        <header className="space-y-1">
          <h1 className="text-2xl font-bold">{contact?.displayName ?? "読み込んでいます…"}</h1>
          <p className="text-text-secondary tabular-nums">
            {contact?.phone}
            {campaign ? `・${campaign.product}` : ""}
          </p>
        </header>

        {banner ? <SuppressionBannerView banner={banner} /> : null}
        {loadFailed ? (
          <p role="alert" className="text-danger">
            情報の一部を読み込めませんでした。確認できるまで発信はできません。
          </p>
        ) : null}

        <section aria-labelledby="call-heading" className="space-y-3">
          <h2 id="call-heading" className="text-lg font-bold">
            発信
          </h2>
          {call ? (
            <StatusBadge
              indicator={callIndicator({
                status: call.status,
                conversation: conversationForMode(call.mode),
                connection: "CONNECTED",
              })}
            />
          ) : null}
          {offerCall ? (
            <button
              type="button"
              onClick={placeCall}
              disabled={placing}
              aria-busy={placing}
              className="block min-h-11 rounded-lg bg-accent px-6 font-bold text-white disabled:opacity-50"
            >
              {placing ? "発信しています…" : "発信する"}
            </button>
          ) : null}
          {callError ? (
            <ErrorNotice
              copy={errorCopy(errorKindForCallError(callError.code))}
              detail={callError.reasons.map(blockedReasonLabel).join("・") || undefined}
            />
          ) : null}
        </section>

        {call && !recorded ? <OutcomeBar onSubmit={recordOutcome} /> : null}
        {recorded ? (
          <div role="status" className="space-y-2">
            <p className="font-bold text-success">
              <span aria-hidden="true">✓ </span>
              {recorded}
            </p>
            <Link href="/leads" className="font-bold text-accent underline">
              次の相手へ
            </Link>
          </div>
        ) : null}
      </main>
    </>
  );
}
