import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "MASK Admin",
  description: "Panel administracyjny MASK Market",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="pl">
      <body>{children}</body>
    </html>
  );
}