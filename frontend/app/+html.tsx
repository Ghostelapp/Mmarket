// @ts-nocheck
import { ScrollViewStyleReset } from "expo-router/html";
import type { PropsWithChildren } from "react";

export default function Root({ children }: PropsWithChildren) {
  return (
    <html lang="en" style={{ height: "100%" }}>
      <head>
        <meta charSet="utf-8" />
        <meta httpEquiv="X-UA-Compatible" content="IE=edge" />
        <meta
          name="viewport"
          content="width=375, initial-scale=1, user-scalable=no, viewport-fit=cover"
        />
        <meta
          httpEquiv="Content-Security-Policy"
          content="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https:; connect-src 'self' https: http://localhost:* ws://localhost:* wss:; base-uri 'self'; form-action 'self'; object-src 'none'"
        />
        {/*
          Disable body scrolling on web to make ScrollView components work correctly.
          If you want to enable scrolling, remove `ScrollViewStyleReset` and
          set `overflow: auto` on the body style below.
        */}
        <ScrollViewStyleReset />
        <style
          dangerouslySetInnerHTML={{
            __html: `
              html, body {
                height: 100%;
                margin: 0;
                padding: 0;
              }
              body > div:first-child {
                width: 375px;
                height: 100vh;
                max-height: 800px;
                position: relative !important;
                overflow: hidden;
                margin: 0 auto;
              }
              [role="tablist"] [role="tab"] * { overflow: visible !important; }
              [role="heading"], [role="heading"] * { overflow: visible !important; }
            `,
          }}
        />
      </head>
      <body
        style={{
          margin: 0,
          padding: 0,
          height: "100%",
          overflow: "hidden",
          backgroundColor: "#000",
        }}
      >
        {children}
      </body>
    </html>
  );
}
