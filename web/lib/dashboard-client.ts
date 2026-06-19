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
export const DASHBOARD_SIM_JSON_URL = "/dashboard_payload.sim.json";

export async function fetchDashboardPayloadFrom(url: string): Promise<DashboardPayload> {
  const res = await fetch(`${url}?t=${Date.now()}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`HTTP ${res.status} loading dashboard payload`);
  }
  const payload = (await res.json()) as DashboardPayload;
  validatePayloadShape(payload);
  return payload;
}

export function fetchDashboardPayload(): Promise<DashboardPayload> {
  return fetchDashboardPayloadFrom(DASHBOARD_JSON_URL);
}

export function fetchSimDashboardPayload(): Promise<DashboardPayload> {
  return fetchDashboardPayloadFrom(DASHBOARD_SIM_JSON_URL);
}
