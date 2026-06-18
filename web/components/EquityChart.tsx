"use client";

import {
  CategoryScale,
  Chart as ChartJS,
  Filler,
  Legend,
  LineElement,
  LinearScale,
  PointElement,
  Tooltip,
} from "chart.js";
import { Line } from "react-chartjs-2";

import { fmtMoney } from "@/lib/format";
import type { EquityPoint } from "@/lib/types";

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Filler,
  Tooltip,
  Legend,
);

interface EquityChartProps {
  curve: EquityPoint[];
  currency: string;
}

export function EquityChart({ curve, currency }: EquityChartProps) {
  const labels = curve.map((p) => p.date);
  const gridColor = "rgba(74, 74, 74, 0.4)";

  return (
    <div className="chart-wrap">
      <Line
        data={{
          labels,
          datasets: [
            {
              label: "Equity total",
              data: curve.map((p) => p.equity_total),
              borderColor: "#6efa5f",
              backgroundColor: "rgba(110, 250, 95, 0.08)",
              fill: true,
              tension: 0.35,
              pointRadius: curve.length > 30 ? 0 : 3,
              borderWidth: 2,
            },
            {
              label: "Corto",
              data: curve.map((p) => p.equity_short),
              borderColor: "#00bfff",
              borderWidth: 1.5,
              tension: 0.35,
              pointRadius: 0,
            },
            {
              label: "Largo",
              data: curve.map((p) => p.equity_long),
              borderColor: "#76b900",
              borderWidth: 1.5,
              tension: 0.35,
              pointRadius: 0,
            },
          ],
        }}
        options={{
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: "index", intersect: false },
          plugins: {
            legend: { display: false },
            tooltip: {
              backgroundColor: "rgba(26,26,26,0.95)",
              titleFont: { family: "Roboto Mono" },
              bodyFont: { family: "Roboto Mono" },
              callbacks: {
                label: (ctx) => {
                  const v = ctx.parsed.y;
                  if (v == null) return ctx.dataset.label ?? "";
                  return `${ctx.dataset.label}: ${fmtMoney(v, currency)}`;
                },
              },
            },
          },
          scales: {
            x: {
              ticks: { color: "#bdbdbd", font: { family: "Roboto Mono", size: 10 } },
              grid: { color: gridColor },
            },
            y: {
              ticks: {
                color: "#bdbdbd",
                font: { family: "Roboto Mono", size: 10 },
                callback: (v) => fmtMoney(Number(v), currency),
              },
              grid: { color: gridColor },
            },
          },
        }}
      />
    </div>
  );
}
