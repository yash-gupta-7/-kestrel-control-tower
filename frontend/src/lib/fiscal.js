// Mirrors backend/app/date_utils.py exactly (year-ending FY label
// convention: Apr-Jun 2026 is "FY2027 Q1"). Duplicated intentionally
// rather than adding a new backend endpoint just to expose this pure
// function -- see DECISIONS.md. If the two ever disagree, the backend
// (which is what actually computes and returns the numbers) wins; this
// copy is for building the quarter picker and default filters only.
//
// DATA_MAX_DATE mirrors backend db.data_max_order_date(): this is a fixed
// historical dataset (Jan 2025 - 30 Jun 2026), not a live feed, so it is
// safe to hardcode rather than fetch.
export const DATA_MAX_DATE = "2026-06-30";
export const DATA_MIN_DATE = "2025-01-01";

export function fiscalYear(dateStr) {
  const d = new Date(dateStr + "T00:00:00");
  const month = d.getMonth() + 1;
  return month >= 4 ? d.getFullYear() + 1 : d.getFullYear();
}

export function fiscalQuarter(dateStr) {
  const d = new Date(dateStr + "T00:00:00");
  const month = d.getMonth() + 1;
  if ([4, 5, 6].includes(month)) return 1;
  if ([7, 8, 9].includes(month)) return 2;
  if ([10, 11, 12].includes(month)) return 3;
  return 4;
}

function pad(n) {
  return String(n).padStart(2, "0");
}

export function fiscalQuarterBounds(fy, fq) {
  const startMonth = { 1: 4, 2: 7, 3: 10, 4: 1 }[fq];
  const startYear = fq !== 4 ? fy - 1 : fy;
  const start = `${startYear}-${pad(startMonth)}-01`;
  let end;
  if (startMonth === 10) end = `${startYear}-12-31`;
  else if (startMonth === 1) end = `${startYear}-03-31`;
  else if (startMonth === 4) end = `${startYear}-06-30`;
  else end = `${startYear}-09-30`;
  return { start, end };
}

export function lastCompleteFiscalQuarter() {
  let fy = fiscalYear(DATA_MAX_DATE);
  let fq = fiscalQuarter(DATA_MAX_DATE);
  let { start, end } = fiscalQuarterBounds(fy, fq);
  if (DATA_MAX_DATE >= end) return { fy, fq, start, end };
  if (fq === 1) {
    fy -= 1;
    fq = 4;
  } else {
    fq -= 1;
  }
  ({ start, end } = fiscalQuarterBounds(fy, fq));
  return { fy, fq, start, end };
}

export function fiscalLabel(fy, fq) {
  return `FY${fy} Q${fq}`;
}

// All fiscal quarters fully or partially covered by the dataset, most
// recent first -- for the quarter picker.
export function listFiscalQuarters() {
  const quarters = [];
  let fy = fiscalYear(DATA_MAX_DATE);
  let fq = fiscalQuarter(DATA_MAX_DATE);
  // eslint-disable-next-line no-constant-condition
  while (true) {
    const bounds = fiscalQuarterBounds(fy, fq);
    if (bounds.end < DATA_MIN_DATE) break;
    quarters.push({ fy, fq, ...bounds, label: fiscalLabel(fy, fq) });
    if (fq === 1) {
      fy -= 1;
      fq = 4;
    } else {
      fq -= 1;
    }
  }
  return quarters;
}

export function lastCompleteCalendarMonth() {
  const [y, m] = DATA_MAX_DATE.split("-").map(Number);
  return `${y}-${pad(m)}`;
}
