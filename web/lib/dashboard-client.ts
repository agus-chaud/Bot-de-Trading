import type { DashboardPayload } from "@/lib/types";

const REQUIRED_KEYS: (keyof DashboardPayload)[] = [
  "meta",
  "data_freshness",
  "equity_curve",
  "positions",
  "recent_fills",
  "risk",
  "kpis",
  "alerts",
  "generated_at",
  "export_version",
];

export function validatePayloadShape(payload: DashboardPayload): void {
  for (const key of REQUIRED_KEYS) {
    if (!(key in payload)) {
      throw new Error(`Payload missing required key: ${key}`);
    }
  }
}

export const DASHBOARD_JSON_URL = "/dashboard_payload.json";

export async function fetchDashboardPayload(): Promise<DashboardPayload> {
  const res = await fetch(`${DASHBOARD_JSON_URL}?t=${Date.now()}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`HTTP ${res.status} loading dashboard payload`);
  }
  const payload = (await res.json()) as DashboardPayload;
  validatePayloadShape(payload);
  return payload;
}
