import type { Metadata, Viewport } from "next";
import { Nav } from "../components/Nav";
import "katex/dist/katex.min.css";
import "./globals.css";

export const metadata: Metadata = {
  applicationName: "LLC",
  title: {
    default: "LLC",
    template: "%s · LLC",
  },
  description:
    "Local-first language learning for laboratory and life-science professionals.",
  openGraph: {
    title: "Laboratory Language Companion",
    description:
      "Local-first language learning for laboratory and life-science professionals.",
    type: "website",
  },
  appleWebApp: {
    capable: true,
    title: "LLC",
  },
};

export const viewport: Viewport = {
  themeColor: "#0b0f16",
};
export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="es" style={{ colorScheme: "dark" }}>
      <body>
        <Nav />
        <main>{children}</main>
        <footer>LLC · local-first · tus datos se quedan contigo</footer>
      </body>
    </html>
  );
}
