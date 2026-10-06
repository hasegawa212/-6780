import type { NextConfig } from "next";

// 画面と API を同じオリジンに見せる（Cookie セッション・CSRF のため）。/v1/* は API サーバーへ転送する
const apiOrigin = process.env.API_ORIGIN ?? "http://127.0.0.1:8080";

const config: NextConfig = {
  poweredByHeader: false,
  // ワークスペースのパッケージは TypeScript の NodeNext 流儀で `./x.js` と書いて `./x.ts` を指す。
  // この対応付けは webpack の extensionAlias でだけ確認できた（Turbopack は UNKNOWN）ため、dev / build は --webpack で動かす
  experimental: {
    extensionAlias: { ".js": [".ts", ".tsx", ".js"] },
  },
  async rewrites() {
    return [{ source: "/v1/:path*", destination: `${apiOrigin}/v1/:path*` }];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        ],
      },
    ];
  },
};

export default config;
