import { NextResponse } from "next/server";

const API_URL = process.env.API_URL ?? "http://localhost:8000";

export async function GET() {
  const res = await fetch(`${API_URL}/health`);
  if (!res.ok) {
    return NextResponse.json(
      { error: "Backend unavailable" },
      { status: res.status },
    );
  }
  const data = await res.json();
  return NextResponse.json(data);
}
