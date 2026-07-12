import type { Metadata } from "next";
import { Nav } from "../components/Nav";
import "./globals.css";

export const metadata: Metadata = {
  title: "DeutschOS",
  description: "Tu sistema local para aprender alemán",
};
export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="es">
      <body>
        <Nav />
        <main>{children}</main>
        <footer>DeutschOS · local-first · tus datos se quedan contigo</footer>
      </body>
    </html>
  );
}
