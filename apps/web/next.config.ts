import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  devIndicators: false,
  logging: {
    incomingRequests: {
      // Next's dev logger has no URL formatter. Suppress only cursor-bearing
      // lines: direct replay, one encoded `from`, or one/two encoded `next`.
      ignore: [
        /^\/(?:discover|api\/discover\/feed)\/?\?(?:[^#]*&)?cursor=/i,
        /^\/applications\/[1-9]\d*\/?\?(?:[^#]*&)?from=%2fdiscover%3f[^#]*%(?:3f|26)cursor%3d/i,
        /^\/(?:login|register)\/?\?(?:[^#]*&)?next=%2f(?:discover%3f[^#]*%(?:3f|26)cursor%3d|applications%2f[1-9]\d*%3ffrom%3d%252fdiscover%253f[^#]*%(?:253f|2526)cursor%253d)/i,
      ],
    },
  },
  headers() {
    return ["/verify-email", "/reset-password"].map((source) => ({
      source,
      headers: [
        { key: "Referrer-Policy", value: "no-referrer" },
        { key: "Cache-Control", value: "no-store" },
      ],
    }));
  },
};

export default nextConfig;
