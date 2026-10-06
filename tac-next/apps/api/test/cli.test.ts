import { describe, expect, it } from "vitest";
import { parseCreateUserArgs } from "../src/cli/create-user-args.js";

/* 運用 CLI `create-user` の引数。パスワードは引数で受け取らない（ps やシェルの履歴に残るため） */

describe("parseCreateUserArgs", () => {
  it("新しい組織の OWNER を作る引数", () => {
    expect(
      parseCreateUserArgs([
        "--email",
        "owner@example.test",
        "--display-name",
        "初期管理者",
        "--role",
        "OWNER",
        "--new-org",
        "新規不動産株式会社",
      ]),
    ).toEqual({
      ok: true,
      value: {
        email: "owner@example.test",
        displayName: "初期管理者",
        role: "OWNER",
        organization: { newName: "新規不動産株式会社" },
      },
    });
  });

  it("既存の組織に追加する引数", () => {
    const r = parseCreateUserArgs([
      "--email=op@example.test",
      "--display-name=担当",
      "--role=OPERATOR",
      "--org-id=00000000-0000-4000-8000-000000000001",
    ]);
    expect(r).toMatchObject({
      ok: true,
      value: { organization: { id: "00000000-0000-4000-8000-000000000001" } },
    });
  });

  it.each([
    [
      ["--email", "a@example.test", "--display-name", "x", "--role", "OWNER"],
      "--org-id か --new-org",
    ],
    [
      [
        "--email",
        "a@example.test",
        "--display-name",
        "x",
        "--role",
        "OWNER",
        "--org-id",
        "o",
        "--new-org",
        "n",
      ],
      "--org-id か --new-org",
    ],
    [["--display-name", "x", "--role", "OWNER", "--new-org", "n"], "--email"],
    [
      [
        "--email",
        "a@example.test",
        "--display-name",
        "x",
        "--role",
        "OWNER",
        "--new-org",
        "n",
        "--password",
        "pw",
      ],
      "--password",
    ],
  ])("不正な引数 %j は失敗（%s）", (argv, hint) => {
    const r = parseCreateUserArgs(argv);
    expect(r.ok).toBe(false);
    expect(!r.ok && r.error).toContain(hint);
  });
});
