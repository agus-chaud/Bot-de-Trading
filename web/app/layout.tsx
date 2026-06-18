import type { Metadata } from "next";
import { Roboto_Mono } from "next/font/google";

import "./globals.css";

const robotoMono = Roboto_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-tech",
});

export const metadata: Metadata = {
  title: "Bot de Trading — Paper-Live Monitor",
  description: "Dashboard read-only del paper-live (equity, posiciones, riesgo, KPIs)",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es">
      <body className={robotoMono.className}>{children}</body>
    </html>
  );
}
