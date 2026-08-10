import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export function GET() {
  return NextResponse.json({
    status: "ok",
    service: "llc-web",
    runtime: process.env.LLC_RUNTIME_MODE ?? "development",
    installation_id: process.env.LLC_INSTALLATION_ID ?? "development",
  });
}
