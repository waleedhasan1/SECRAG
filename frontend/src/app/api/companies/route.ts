import { NextResponse } from "next/server";

const API_URL = process.env.API_URL ?? "http://localhost:8000";

export async function GET() {
  const res = await fetch(`${API_URL}/companies`);
  if (!res.ok) {
    return NextResponse.json(
      { error: "Failed to fetch companies" },
      { status: res.status },
    );
  }
  const data = await res.json();
  return NextResponse.json(data);
}
