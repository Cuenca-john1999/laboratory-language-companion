import type { Metadata, Viewport } from "next";
import { Nav } from "../components/Nav";
import "katex/dist/katex.min.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "DeutschOS",
  description: "Tu sistema local para aprender alemán",
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
        <footer>DeutschOS · local-first · tus datos se quedan contigo</footer>
      </body>
    </html>
  );
}
