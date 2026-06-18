import { DashboardView } from "@/components/DashboardView";
import { readDashboardPayloadFromDisk } from "@/lib/dashboard-server";

export default function HomePage() {
  const initialData = readDashboardPayloadFromDisk();
  return <DashboardView initialData={initialData} />;
}
