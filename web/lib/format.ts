export function fmtMoney(value: number | null | undefined, currency = "ARS"): string {
  if (value == null || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("es-AR", {
    style: "currency",
    currency,
    maximumFractionDigits: 0,
  }).format(value);
}

export function fmtNum(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(value)) return "—";
  return Number(value).toFixed(digits);
}

export function fmtPct(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${(Number(value) * 100).toFixed(2)}%`;
}
