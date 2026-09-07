# IntelliOps Frontend

AI-Powered Cloud Operations Platform UI — includes the SaaS landing page, interactive dashboard, live metrics, service health monitors, alert streams, and audit logs.

## Features

- **Landing Page**: Modern SaaS marketing page with live architecture preview, interactive demo trigger, metrics, and documentation links.
- **Dashboard**:
  - **Overview**: System-wide telemetry, AI root-cause anomaly indicators, service status, and live alert ticker.
  - **Alerts**: Searchable, filterable real-time alert feed by severity (Critical, Warning, Info, Resolved) and acknowledgement status.
  - **Services**: Service health matrix, 30-day uptime track, sparklines, latency/error rate, and deep-dive drawer panels.
  - **Metrics**: Interactive Prometheus-backed time series charts with multi-window filters (1h, 6h, 24h, 7d).
- **Audit Log**: Chronological trail of deployment events, auth events, configuration changes, and alerts with severity badges and search.
- **Product & Docs**: Product overview and developer documentation pages.

## Tech Stack

- **Framework**: React 19 + TypeScript
- **Bundler**: Vite
- **Styling**: Tailwind CSS v4 + Vanilla CSS Design Tokens
- **Typography**: Inter & JetBrains Mono

## Getting Started

```bash
# Install dependencies
npm install

# Start development server
npm run dev

# Build for production
npm run build

# Preview production build
npm run preview
```
