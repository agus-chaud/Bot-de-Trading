import fs from "fs";
import path from "path";

import { validatePayloadShape } from "@/lib/dashboard-client";
import type { DashboardPayload } from "@/lib/types";

export function readDashboardPayloadFromDisk(): DashboardPayload {
  const candidates = [
    path.join(process.cwd(), "public", "dashboard_payload.json"),
    path.join(process.cwd(), "fixtures", "dashboard_payload.json"),
    path.join(process.cwd(), "..", "data", "dashboard_payload.json"),
  ];

  for (const filePath of candidates) {
    if (fs.existsSync(filePath)) {
      const payload = JSON.parse(fs.readFileSync(filePath, "utf-8")) as DashboardPayload;
      validatePayloadShape(payload);
      return payload;
    }
  }

  throw new Error("dashboard_payload.json not found — run: npm run prebuild");
}
