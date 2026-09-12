// Seeded/mock display data for the Dashboard view. Meridian's real
// persistence (meridian_submissions / meridian_reports tables) is minimal by
// design — this module fills in a believable enterprise-reporting picture
// around the live counts pulled from /api/reports so the dashboard reads as
// a credible product rather than an empty shell.

export interface RecentReport {
  id: string;
  client: string;
  period: string;
  summary: string;
  status: "Final" | "Draft" | "Under Review";
  generatedAt: string;
}

export interface PeriodStatus {
  period: string;
  label: string;
  status: "Open" | "Closing" | "Locked";
  submissionsExpected: number;
  submissionsReceived: number;
  dueDate: string;
}

export const seededRecentReports: RecentReport[] = [
  {
    id: "RPT-10231",
    client: "Northwind Industrial",
    period: "FY26-Q2",
    summary: "Consolidated revenue & expense reconciliation",
    status: "Final",
    generatedAt: "2026-08-24T14:12:00Z",
  },
  {
    id: "RPT-10230",
    client: "Alden Capital Partners",
    period: "FY26-Q2",
    summary: "Segment-level margin analysis",
    status: "Final",
    generatedAt: "2026-08-23T09:41:00Z",
  },
  {
    id: "RPT-10229",
    client: "Cascade Retail Group",
    period: "FY26-Q2",
    summary: "Working capital & liquidity summary",
    status: "Under Review",
    generatedAt: "2026-08-22T17:03:00Z",
  },
  {
    id: "RPT-10228",
    client: "Northwind Industrial",
    period: "FY26-Q1",
    summary: "Prior-period restated financials",
    status: "Final",
    generatedAt: "2026-08-19T11:27:00Z",
  },
];

export const seededPeriods: PeriodStatus[] = [
  {
    period: "FY26-Q2",
    label: "FY26 — Q2 Close",
    status: "Closing",
    submissionsExpected: 42,
    submissionsReceived: 37,
    dueDate: "2026-08-29",
  },
  {
    period: "FY26-Q1",
    label: "FY26 — Q1 Close",
    status: "Locked",
    submissionsExpected: 40,
    submissionsReceived: 40,
    dueDate: "2026-05-30",
  },
];

export const seededAggregateFigures = {
  totalSubmittedYtd: 128_940_211.32,
  totalReportsYtd: 118,
  avgProcessingHours: 6.4,
  reconciliationRate: 0.987,
};

export const seededClients = [
  "Northwind Industrial",
  "Alden Capital Partners",
  "Cascade Retail Group",
  "Summit Health Systems",
  "Beacon Logistics",
];
