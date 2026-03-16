# AgentWeave Dashboard — Build Spec

## Goal
Build a self-contained React dashboard UI for AgentWeave observability.
Replaces Grafana for agent-specific metrics. Inspired by Langfuse + LangSmith.

## Design Direction
- **Dark mode by default** — bg `#0a0a0f`, surface `#111118`, border `#1e1e2e`
- **Accent color:** Indigo/violet — `#7c3aed` primary, `#a78bfa` highlight
- **Font:** Inter (Google Fonts)
- **Vibe:** Clean, dense, developer-tool aesthetic — like Langfuse meets Grafana

## Tech Stack
- Vite + React 18 + TypeScript
- Tailwind CSS v3 (dark mode: class strategy)
- Recharts for all charts (line, bar, area)
- date-fns for time formatting
- NO backend — queries Tempo + Prometheus directly from browser (CORS handled via proxy)

## Data Sources
All queries go through the AgentWeave proxy or direct to internal APIs:
- **Tempo HTTP:** `http://192.168.1.70:31989`
- **Prometheus:** `http://192.168.1.70:30300/api/datasources/proxy/uid/prometheus` (via Grafana proxy, basic auth admin:observability123)

For production, these should be configurable via env vars (`VITE_TEMPO_URL`, `VITE_PROMETHEUS_URL`, `VITE_GRAFANA_URL`, `VITE_GRAFANA_AUTH`).

## Panels / Sections

### Row 1: Stat Cards (4 cards)
1. **Total LLM Calls** — Tempo TraceQL search count
   - Query: `{ resource.service.name = "agentweave-proxy" && name != "llm.unknown" }` limit=10000
   - Reduction: count of returned traces
   - Icon: activity icon, color: indigo

2. **Total Cost (USD)** — Tempo TraceQL metrics
   - Query: `{ resource.service.name = "agentweave-proxy" } | sum_over_time(span.cost.usd)`
   - Display: `$X.XX`
   - Icon: dollar icon, color: emerald

3. **Cache Hit Rate** — Tempo TraceQL metrics  
   - Query: `{ resource.service.name = "agentweave-proxy" } | avg_over_time(span.cache.hit_rate)`
   - Display: `XX.X%`
   - Icon: bolt icon, color: amber

4. **Avg Turns/Task** — Tempo TraceQL metrics
   - Query: `{ resource.service.name = "agentweave-proxy" } | avg_over_time(span.agent.turn_count)`
   - Display: `X.X`
   - Icon: refresh icon, color: cyan

### Row 2: Time Series Charts (2 wide panels)
5. **LLM Calls over Time** — line chart
   - Prometheus: `rate(traces_spanmetrics_calls_total{service="agentweave-proxy"}[5m]) * 300`
   - X-axis: time, Y-axis: calls per 5 min

6. **Cost over Time (USD)** — area chart
   - Tempo traceqlmetrics: `{ resource.service.name = "agentweave-proxy" } | sum_over_time(span.cost.usd)`
   - Per 5-minute bucket

### Row 3: Bar Charts (2 panels)
7. **Calls by Model** — horizontal bar
   - Prometheus: `sum by (prov_llm_model) (increase(traces_spanmetrics_calls_total{service="agentweave-proxy"}[$range]))`

8. **P95 Latency by Model** — horizontal bar (ms)
   - Prometheus: `histogram_quantile(0.95, sum by (le, prov_llm_model) (rate(traces_spanmetrics_latency_bucket{service="agentweave-proxy"}[5m]))) * 1000`

### Row 4: Full-width Table
9. **Recent LLM Calls** — sortable table
   - Columns: Time, Trace ID (linked), Model, Latency (ms), Tokens (in/out), Cost (USD), Session ID
   - Tempo search: `{ resource.service.name = "agentweave-proxy" && name != "llm.unknown" }` limit=50
   - Click trace ID → expand row showing full span attributes

## Time Range Selector
- Dropdown: Last 1h / 3h / 6h / 24h / 7d
- Default: Last 6h
- Auto-refresh: every 60s with "last updated" timestamp

## Header
- Logo: "⚡ AgentWeave" in Inter bold
- Subtitle: "Agent Activity Dashboard"
- Time range selector (right side)
- "Refresh" button

## Error States
- If Tempo unreachable: show amber warning banner "Tempo unavailable — showing cached data"
- Individual panels: show skeleton loader while fetching, then data or "No data" state

## File Structure
```
dashboard/
├── package.json
├── vite.config.ts
├── tailwind.config.js
├── tsconfig.json
├── index.html
├── src/
│   ├── main.tsx
│   ├── App.tsx
│   ├── components/
│   │   ├── StatCard.tsx
│   │   ├── TimeSeriesChart.tsx
│   │   ├── BarChart.tsx
│   │   ├── TraceTable.tsx
│   │   └── Header.tsx
│   ├── hooks/
│   │   ├── useTempo.ts      # TraceQL search + traceqlmetrics
│   │   └── usePrometheus.ts # Prometheus query
│   └── lib/
│       └── queries.ts       # All query strings + transformers
├── Dockerfile
└── nginx.conf
```

## Dockerfile
```dockerfile
FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
```

## nginx.conf — IMPORTANT: must proxy /tempo and /prometheus to avoid CORS
```nginx
server {
  listen 80;
  root /usr/share/nginx/html;
  
  location /tempo/ {
    proxy_pass http://192.168.1.70:31989/;
  }
  
  location /prometheus/ {
    proxy_pass http://192.168.1.70:30300/api/datasources/proxy/uid/prometheus/;
    proxy_set_header Authorization "Basic YWRtaW46b2JzZXJ2YWJpbGl0eTEyMw==";
  }
  
  location / {
    try_files $uri $uri/ /index.html;
  }
}
```

So in the React app, use `/tempo/api/...` and `/prometheus/api/v1/...` as the base URLs.

## k8s Deployment
After `npm run build`, package into Docker image and push to `localhost:5000/agentweave-dashboard:latest`.
Service NodePort: 30895.

## Quality Bar
- All panels must show real data (tested against live Tempo/Prometheus)
- No TypeScript errors
- Mobile-responsive (stats stack 2x2 on mobile)
- Loading skeletons, not blank panels
- Looks good enough to screenshot for the LinkedIn post
