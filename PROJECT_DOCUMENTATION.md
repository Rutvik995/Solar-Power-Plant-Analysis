# Solar Power Plant Analysis — Comprehensive Project Documentation

## Executive Overview

The **Solar Power Plant Analysis Platform** is an enterprise-grade operational intelligence and conversational analytics system designed for commercial and utility-scale solar photovoltaic (PV) facilities.

The platform bridges the gap between raw SCADA/IoT telemetry and operational decision-making by pairing an **interactive visual telemetry dashboard** with a **deterministic, multi-agent AI copilot** built on **LangGraph**, **FastAPI**, and **React**.

### The Core Architectural Axiom
Standard Large Language Models (LLMs) struggle with mission-critical engineering applications due to quantitative hallucination. This platform eliminates hallucination by strictly enforcing:

> **"The LLM plans, coordinates, and explains; pure Python and SQL tools compute. All numbers originate from deterministic tool outputs, and an automated grounding validator verifies that every number in the narrative matches upstream data before it reaches the user."**

---

## 1. Solar Power Plant Fundamentals & Domain Concepts

To effectively analyze and operate solar photovoltaic assets, operators and algorithms evaluate specific physical and thermodynamic metrics. This section explains the key terminology and mathematical models governing solar power plants.

### 1.1 Anatomy of a Utility-Scale Solar PV Plant
A commercial solar power plant converts solar irradiance into grid-compliant alternating current (AC) electricity through several hierarchical stages:

```
[ Solar Photons ]
       │
       ▼
[ PV Modules / Solar Panels ] ──(Strings in Series)──► [ Combiner Box ]
                                                            │ (DC Electricity)
                                                            ▼
                                                   [ Solar Inverter ]
                                                            │ (AC Electricity)
                                                            ▼
                                               [ Step-Up Transformer ]
                                                            │ (Medium/High Voltage)
                                                            ▼
                                                    [ Utility Grid ]
```

1. **Photovoltaic (PV) Module / Panel**: Silicon semiconductor cells that generate direct current (DC) electricity via the photovoltaic effect when exposed to sunlight.
2. **String**: Multiple PV modules wired in series to build sufficient DC operating voltage.
3. **Inverter**: The operational heart of the plant. It converts variable DC electricity from PV strings into synchronized 3-phase alternating current (AC) power matching grid frequency and voltage.
4. **Block**: A logical and electrical sub-division of a large solar farm. A plant typically consists of several blocks (e.g., Block A, Block B), each containing dedicated inverters and array structures.
5. **Plant / Facility**: The entire utility-scale solar installation, comprising multiple blocks, weather stations, central substations, and grid interconnection switchgear.

---

### 1.2 Solar Irradiance ($G$) vs. Solar Radiation / Insolation ($H$)

| Concept | Symbol | Units | Definition |
|---|:---:|:---:|---|
| **Solar Irradiance** | $G$ | $\text{W/m}^2$ | Instantaneous rate of radiant energy incident on a surface per unit area (power flux). |
| **Solar Radiation (Insolation)** | $H$ | $\text{kWh/m}^2/\text{day}$ | Total cumulative solar energy accumulated over a period of time (energy integral: $H = \int G \, dt$). |
| **Plane of Array (POA)** | $G_{poa}$ | $\text{W/m}^2$ | Irradiance measured directly on the tilted surface angle of the solar PV modules. |
| **Standard Test Conditions (STC)** | $G_0$ | $1,000\ \text{W/m}^2$ | Industry benchmark reference irradiance at $25^\circ\text{C}$ cell temperature and AM 1.5 spectrum. |

---

### 1.3 Performance Ratio ($PR$)
**Performance Ratio ($PR$)** is the globally recognized gold standard (IEC 61724) for evaluating solar plant efficiency. It measures the relationship between **actual AC energy output** and the **theoretical maximum output** possible under the available sunlight:

$$PR = \frac{\sum \text{Yield}_{\text{actual}} (\text{kWh})}{\sum \left( P_{dc, \text{rated}} (\text{kW}) \times \frac{H_{poa} (\text{kWh/m}^2)}{1\ \text{kW/m}^2} \right)}$$

- **Sunlight Independence**: Because $PR$ is normalized for sunlight ($H_{poa}$), a plant in rainy weather can achieve a healthy $PR$ (e.g., 85%) even with low total MWh, whereas a poorly performing plant under blistering sun might have a depressed $PR$ (e.g., 68%).
- **Healthy Benchmark Ranges**:
  - $\ge 82\%$: **Optimal / High Performance** (clean panels, cool weather, healthy inverters).
  - $75\% - 81\%$: **Warning / Moderate Degradation** (soiling, moderate heat derating, minor clipping).
  - $< 75\%$: **Critical Underperformance** (tripped inverters, severe shading, inverter component failures).
- **Aggregation Invariant**: Fleet and Plant $PR$ are always calculated via pooled total yield divided by pooled capacity-weighted insolation—**never** by taking an unweighted arithmetic average of inverter PRs.

---

### 1.4 Specific Yield ($Y_f$)
**Specific Yield** (expressed in $\text{kWh/kWp}$) normalizes energy generation by the plant's installed DC peak capacity:

$$Y_f = \frac{\text{Energy Produced (kWh)}}{P_{dc, \text{rated}} (\text{kWp})}$$

- **Purpose**: Allows direct comparison between plants or inverters of completely different sizes. For example, a $50\ \text{MWp}$ plant generating $250,000\ \text{kWh}$ and a $10\ \text{MWp}$ plant generating $50,000\ \text{kWh}$ both achieved a Specific Yield of $5.0\ \text{kWh/kWp}$.
- **Typical Daily Range**: $3.5 - 6.5\ \text{kWh/kWp/day}$ depending on geographical location and season.

---

### 1.5 Capacity Factor ($CF$)
**Capacity Factor** measures the ratio of actual energy generated over a period to the hypothetical maximum output if the asset operated at full rated AC capacity 24 hours a day continuously:

$$CF = \frac{\text{Total Daily Yield (kWh)}}{P_{ac, \text{rated}} (\text{kW}) \times 24\ \text{hours}}$$

- **Solar Nature**: Because the sun only shines during daylight (typically 8–10 productive hours), solar capacity factors typically range between **$18\% - 28\%$**, even for top-tier solar facilities.

---

### 1.6 Soiling Loss & Soiling Ratio ($SR$)
**Soiling** refers to the accumulation of dust, dirt, pollen, sand, bird droppings, and industrial pollutants on the protective glass surface of solar modules. This optical barrier reflects and absorbs sunlight, preventing photons from reaching the PV silicon cells:

- **Soiling Ratio ($SR$)**: The transmission cleanliness factor ($0.0$ to $1.0$).
  - $SR = 1.00$: Pristine, newly cleaned module glass.
  - $SR = 0.95$: $5\%$ optical transmission loss due to dust accumulation.
  - $SR = 0.80$: Severe soiling causing a $20\%$ generation penalty.
- **Soiling Energy Loss Formula**:
  $$\text{Energy Lost to Soiling (kWh)} = \text{Actual Yield} \times \left( \frac{1}{SR} - 1 \right)$$
  $$\text{Clean Yield (kWh)} = \text{Actual Yield} + \text{Energy Lost to Soiling}$$
- **Cleaning Events**: A rapid positive jump in $SR$ ($\Delta SR > +0.02$) indicates that modules were washed by plant operators or cleaned by a natural rainfall event.

---

### 1.7 Thermal Efficiency Derating
Silicon photovoltaic cells suffer an efficiency drop as their operating temperature rises. 
- While higher solar irradiance increases power, higher module temperatures decrease cell voltage.
- Standard PV panels carry a temperature coefficient of power ($\gamma \approx -0.35\%\ \text{to}\ -0.45\% / ^\circ\text{C}$ above $25^\circ\text{C}$).
- On hot summer days where ambient air temperature reaches $38^\circ\text{C}$, module glass temperatures frequently exceed $60^\circ\text{C} - 65^\circ\text{C}$, causing an unavoidable thermal efficiency loss of $12\% - 16\%$.

---

### 1.8 Inverter Operational Status Codes
Modern solar inverters log continuous operational health codes:

| Code | Status Meaning | Telemetry & Yield Impact | Action Required |
|:---:|---|---|---|
| **0** | **Normal Generation** | Full power tracking (MPPT active). | None; optimal. |
| **1** | **Standby / Curtailed** | Low sunlight (dawn/dusk) or grid operator curtailment. Excluded from fault counts. | Monitor; expected behavior. |
| **2** | **Warning / Derating** | Non-critical alert (e.g., high internal heat, minor DC voltage ripple). Still generating. | Inspect thermal fans/filters. |
| **3** | **Critical Fault / Tripped** | Inverter tripped on hardware fault (IGBT failure, ground fault, insulation breakdown). Zero power. | Immediate technician dispatch. |
| **4** | **Offline / Disconnected** | Non-communicating or isolated from AC grid. | Verify comms / grid breaker. |

- **Zero-Generation Day**: Defined as any day where daylight solar radiation exceeded $0.5\ \text{kWh/m}^2$, but inverter total yield was $\le 5\ \text{kWh}$.

---

### 1.9 Block Peer Comparison & Anomaly Detection
Weather is highly localized; clouds can shade one sector of a county while another is sunny.
- **Peer Benchmarking**: Comparing an inverter against its neighboring inverters within the **exact same block** on the **exact same day** eliminates irradiance and temperature variations.
- **Modified Z-Score (MAD)**:
  $$M_i = \frac{0.6745 \times (PR_i - \text{median}(PR_{\text{peers}}))}{\text{MAD}}$$
  $$\text{MAD} = \text{median}\left( |PR_i - \text{median}(PR_{\text{peers}})| \right)$$
  Inverters with $M_i < -3.0$ exhibit statistically significant underperformance, isolating equipment defects from weather conditions.

---

## 2. System Architecture

The application comprises three primary tiers:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        React 18 SPA (Frontend)                         │
│  - TailwindCSS + CSS tokens (Light Theme #F8FAFC, #2563EB)             │
│  - Recharts (Bar, Line, Composed, Treemap)                             │
│  - TanStack Query (Caching, Async Data State)                          │
│  - React Router (Overview, Plant Detail, Chat Copilot)                 │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ HTTP / REST & SSE Streaming
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                         FastAPI Server (Backend)                       │
│  - /api/dashboard/* (Deterministic REST endpoints, 60s cache)          │
│  - /api/meta (Asset topology, date bounds, provider settings)          │
│  - /api/chat (LangGraph Orchestration, SSE token streaming)            │
│  - /api/sessions (Conversation persistence in SQLite/PostgreSQL)       │
└──────────────────┬─────────────────────────────────┬───────────────────┘
                   │                                 │
                   ▼                                 ▼
┌──────────────────────────────────────┐  ┌──────────────────────────────┐
│       LangGraph Multi-Agent DAG      │  │     PostgreSQL Database      │
│  - Orchestrator (DAG Planner)        │  │  - plants, blocks, inverters │
│  - DAG Executor (Parallel Layers)    │  │  - daily_weather_telemetry   │
│  - Data / Fault / Perf / Env Agents  │  │  - daily_inverter_telemetry  │
│  - Grounding Validator & Synthesizer │  │                              │
└──────────────────────────────────────┘  └──────────────────────────────┘
```

---

## 3. Database Schema

The PostgreSQL database (`solardata`) stores portfolio topology and daily-aggregated time-series telemetry.

### 3.1 Entity Relationship Diagram

```mermaid
erDiagram
    plants ||--o{ blocks : "contains"
    plants ||--o{ inverters : "owns"
    plants ||--o{ daily_weather_telemetry : "logs"
    blocks ||--o{ inverters : "groups"
    inverters ||--o{ daily_inverter_telemetry : "generates"

    plants {
        int plant_id PK
        varchar plant_name
        numeric area_acres
        numeric capacity_mwp
    }

    blocks {
        int block_id PK
        int plant_id FK
        varchar block_name
    }

    inverters {
        varchar inverter_id PK
        int block_id FK
        int plant_id FK
        numeric rated_dc_kw
        numeric rated_ac_kw
    }

    daily_weather_telemetry {
        date log_date PK
        int plant_id PK, FK
        numeric peak_poa_irradiance_w_m2
        numeric total_solar_radiation_kwh_m2
        numeric avg_ambient_temp_c
        numeric max_module_temp_c
        numeric soiling_ratio
    }

    daily_inverter_telemetry {
        date log_date PK
        varchar inverter_id PK, FK
        int plant_id FK
        numeric peak_dc_power_kw
        numeric peak_ac_power_kw
        numeric total_daily_yield_kwh
        numeric avg_dc_voltage_v
        numeric avg_dc_current_a
        int inverter_status_code
    }
```

---

### 3.2 Table Definitions

#### 1. `plants`
Represents solar installations in the fleet.
| Column | Type | Constraints | Description |
|---|---|---|---|
| `plant_id` | `SERIAL` | `PRIMARY KEY` | Unique plant identifier ($1$ to $5$). |
| `plant_name` | `VARCHAR(100)` | `NOT NULL` | Facility name (e.g. "Solar Alpha", "Solar Beta"). |
| `area_acres` | `NUMERIC(8,2)` | | Physical site footprint in acres ($80 - 250$). |
| `capacity_mwp` | `NUMERIC(8,2)` | `NOT NULL` | Installed peak DC capacity in Megawatt-peak ($20 - 60\ \text{MWp}$). |

#### 2. `blocks`
Physical subsections of a plant grouping inverters.
| Column | Type | Constraints | Description |
|---|---|---|---|
| `block_id` | `SERIAL` | `PRIMARY KEY` | Unique block identifier ($1$ to $21$). |
| `plant_id` | `INT` | `REFERENCES plants(plant_id)` | Parent plant. |
| `block_name` | `VARCHAR(50)` | `NOT NULL` | Block name (e.g. "Block_A", "Block_B"). |

#### 3. `inverters`
Individual DC-to-AC conversion units.
| Column | Type | Constraints | Description |
|---|---|---|---|
| `inverter_id` | `VARCHAR(50)` | `PRIMARY KEY` | Standard identifier (`P{plant}_{block}_INV_{num}`). |
| `block_id` | `INT` | `REFERENCES blocks(block_id)` | Parent block. |
| `plant_id` | `INT` | `REFERENCES plants(plant_id)` | Parent plant. |
| `rated_dc_kw` | `NUMERIC(8,2)` | `NOT NULL` | Installed DC rating ($1,000 - 1,666\ \text{kW}$). |
| `rated_ac_kw` | `NUMERIC(8,2)` | `NOT NULL` | Rated AC capacity ($950 - 1,583\ \text{kW}$). |

#### 4. `daily_inverter_telemetry`
Daily operational records per inverter.
| Column | Type | Constraints | Description |
|---|---|---|---|
| `log_date` | `DATE` | `NOT NULL` | Date of operation (Composite PK). |
| `inverter_id` | `VARCHAR(50)` | `REFERENCES inverters` | Inverter ID (Composite PK). |
| `plant_id` | `INT` | `REFERENCES plants` | Owning plant ID. |
| `peak_dc_power_kw` | `NUMERIC(10,2)` | | Peak DC power recorded during the day. |
| `peak_ac_power_kw` | `NUMERIC(10,2)` | | Peak AC power delivered. |
| `total_daily_yield_kwh` | `NUMERIC(12,2)` | | Total energy generated during the day. |
| `avg_dc_voltage_v` | `NUMERIC(8,2)` | | Average operating DC bus voltage ($700 - 750\ \text{V}$). |
| `avg_dc_current_a` | `NUMERIC(8,2)` | | Average operating DC current in Amperes. |
| `inverter_status_code` | `INT` | Default `0` | Operational status code ($0 = \text{Normal}, 2 = \text{Warning}, 3 = \text{Fault}$). |

#### 5. `daily_weather_telemetry`
Daily meteorological records per plant.
| Column | Type | Constraints | Description |
|---|---|---|---|
| `log_date` | `DATE` | `NOT NULL` | Measurement date (Composite PK). |
| `plant_id` | `INT` | `REFERENCES plants` | Plant ID (Composite PK). |
| `peak_poa_irradiance_w_m2` | `NUMERIC(8,2)` | | Maximum Plane-of-Array irradiance ($\text{W/m}^2$). |
| `total_solar_radiation_kwh_m2` | `NUMERIC(8,2)` | | Cumulative daily solar insolation ($H$). |
| `avg_ambient_temp_c` | `NUMERIC(5,2)` | | Average daily ambient air temperature ($^\circ\text{C}$). |
| `max_module_temp_c` | `NUMERIC(5,2)` | | Maximum PV module surface temperature ($^\circ\text{C}$). |
| `soiling_ratio` | `NUMERIC(5,2)` | | Glass transmission cleanliness factor ($0.0 - 1.0$). |

---

### 3.3 Temporal Dataset & Injected Benchmark Scenarios
The database contains **113 continuous operational days** spanning **July 1, 2026 through October 21, 2026** ($13,108$ inverter records, $565$ weather records).

Specific benchmark events are embedded in the dataset for validation:
1. **Complete Inverter Hardware Trip (2026-09-16, Plant 1, `P1_Block_A_INV_02`)**:
   - Zero generation ($\text{Yield} = 0.00\ \text{kWh}$, $\text{Peak DC} = 0.00\ \text{kW}$) despite abundant irradiance ($6.66\ \text{kWh/m}^2$).
   - Logged with `inverter_status_code = 2`, while block peers generated at $PR \approx 85\%$.
2. **Localized Shading / String Failure (2026-09-21, Plant 2, `P2_Block_B_INV_04`)**:
   - Partial output drop to $35\%$ of capacity, logged with `status_code = 3`.
3. **Plant-Wide Thermal Efficiency Degradation (2026-09-23, Plant 5)**:
   - Module temperature spiked to $64.0^\circ\text{C}$, inducing a $15\%$ drop in $PR$ from heat derating.
4. **Dust Accumulation Loss (2026-09-19 to 2026-09-26, Plant 3)**:
   - Prolonged arid period dropped soiling ratio to $0.78$ ($22\%$ optical loss).
5. **August Dust Accumulation & Cleaning Event (Plant 4)**:
   - August 5–14: Soiling ratio deteriorated by $1\%$ daily down to $0.85$.
   - August 15: Module washing restored soiling ratio to $0.98$.

---

## 4. Comprehensive Feature Guide

### 4.1 Global Filter Bar
Located sticky at the top of the analytics dashboard:
- **Plant Asset Selector**: Switch between fleet-wide view ("All Plants") or focus on a specific asset (Plant 1–5).
- **Date Presets**: One-click filters for **7D**, **28D** (default last 28 days), **30D**, **July**, **August**, **September**, **October**, and **All**.
- **Date Range Picker**: Native dual date inputs bounded dynamically by portfolio availability (`/api/meta`).
- **Compare Previous Period Toggle**: Computes deltas versus the preceding equivalent time window. Displays single explanatory note if historical data is bounded.
- **Aggregation Granularity**: Switch chart bucketing between **Day**, **Week**, and **Month**.

---

### 4.2 Fleet Overview Tab

#### 1. Seven High-Level KPI Cards
Rendered in a responsive 4 + 3 grid on screens under 1400px:
1. **Energy Generated (MWh)**: Total AC electrical power delivered (Icon: `Zap`).
2. **Performance Ratio ($PR\ %$)**: Weather-adjusted efficiency with historical teal sparkline (Icon: `Gauge`).
3. **Specific Yield ($\text{kWh/kWp}$)**: Generation normalized by DC size (Icon: `Sun`).
4. **Capacity Factor ($\%$)**: 24-hour plant utilization factor (Icon: `Percent`).
5. **Soiling Loss ($\%$)**: Generation penalty from dusty panels; inverted red/green delta logic (Icon: `Droplets`).
6. **Availability ($\%$)**: Uptime fraction of inverters ready to generate (Icon: `CheckCircle2`).
7. **Open Alerts**: Flagged inverter-day count (zero-gen or status faults; inverted delta logic; Icon: `AlertTriangle`).
- **Interactive Glossary Tooltips**: Hovering the `(?)` icon reveals plain-English definitions from `glossary.yaml`. Tooltip icon is placed inline with labels to prevent badge collisions across responsive breakpoints.

#### 2. Fleet PR Benchmark Bar Chart
- X-axis bounded dynamically to $[\min - 2, \max + 1]\%$.
- Plant names formatted with wide left margin to prevent text wrapping.
- Exact PR values formatted directly at the bar ends.
- High-contrast Reference Line marking fleet-average $PR$.

#### 3. Dual-Axis Generation & Irradiance Timeline
- Composed chart overlaying total daily generation bars (blue, MWh) against solar radiation lines (amber, $\text{kWh/m}^2$).

#### 4. Plant Comparison & Health Matrix
- Sortable table listing all plants with capacity, generated MWh, $PR$, and specific yield.
- Color-coded health badges based on fleet median delta thresholds (`Optimal`, `Warning`, `Critical`).

---

### 4.3 Plant Deep-Dive Detail Tab

#### 1. Block PR Comparison Bars
- Visualizes $PR$ for each physical block.
- Highlights underperforming blocks in red if $PR$ is $> 3.0$ percentage points below the plant median.

#### 2. Inverter Leaderboards
- **Best 5 Inverters**: Top performers by $PR$.
- **Worst 5 Inverters**: Bottom performers sorted ascending by $PR$ (ties broken deterministically by ID). Highlighted in red only if underperforming the plant median by $> 3.0$ points.

#### 3. Inverter × Date Telemetry Heatmap
- Dense interactive matrix with sticky left inverter-id column and horizontal date scrolling.
- **Toggle: "Absolute PR | Difference from peers"**:
  - **Absolute Mode**: Standard red ($<70\%$), amber ($70-85\%$), lime ($86-90\%$), green ($\ge 91\%$) scale.
  - **Difference Mode**: Compares each inverter cell to that day's plant median $PR$:
    - **Grey**: Within $\pm 1.0\ \text{pp}$ (peer baseline).
    - **Amber**: Deficit of $-1.0\ \text{to}\ -3.0\ \text{pp}$.
    - **Red**: Severe deficit of $< -3.0\ \text{pp}$.
    - **Blue**: Performance surplus $> +1.0\ \text{pp}$.
- Clicking any cell or inverter tag opens the Slide-Out Inverter Drawer.

#### 4. Topology Treemap
- Nested Recharts Treemap representing the hierarchy: `Plant → Block → Inverter`.
- Outer block containers styled with solid borders and uppercase block name header bars.
- Child inverter tiles sized by DC rated capacity ($\text{kW}$) and colored by $PR$.
- **Luminance-Adaptive Typography**: Dark text (`#0F172A`) on bright/light tiles (lime, amber, grey) and crisp white text (`#FFFFFF`) on dark tiles (red, dark green).
- Rich interactive tooltips and native SVG `<title>` displaying inverter tag, block name, $PR$, and rated kW.

#### 5. Weather & Thermal Telemetry Panels
- **Thermal Chart**: Ambient air temperature vs. module surface temperature ($^\circ\text{C}$).
- **Irradiance & Soiling Chart**: Solar radiation ($H$) plotted alongside violet soiling ratio line ($SR$).

---

### 4.4 Slide-Out Inverter Drawer
- Right-aligned panel covering the full viewport height (`fixed inset-y-0 right-0 h-screen`), closing on `Esc` key or backdrop click.
- **Summary Header**: Block location, rated DC/AC kW, and flagged days badge.
- **Operating Status Timeline**: Color-coded day-by-day micro-strip (green = normal, amber = warning, red = fault/zero-gen).
- **PR vs. Block Peer Median Chart**: Inverter $PR$ line compared against a high-contrast dark dashed line (`#0F172A`, width 2.5) with data dots and legend.
- **Daily Yield Chart**: Bar chart with dedicated Y-axis width and unclipped labels.
- **DC Electrical Characteristics Chart**: Dual-axis voltage (V) and current (A) telemetry.
- **"Ask AI about this inverter" Button**: Automatically formulates an intelligent query regarding the worst fault date and opens the AI Chat Copilot in a new session.

---

### 4.5 AI Copilot Multi-Agent Assistant

#### Multi-Agent LangGraph Workflow
When a user asks a complex question (e.g., *"Why did inverter P1_Block_A_INV_02 fail on 16 Sept 2026?"*), the request is processed by a coordinated graph:

```text
[ User Query ]
      │
      ▼
[ Orchestrator (Planner LLM) ] ──(Generates Pydantic DAG Plan)
      │
      ▼
[ DAG Executor ] ──(Concurrent Topological Layers)
      │
      ├──► Data Agent: AST-guarded SQL query execution
      ├──► Performance Agent: PR, specific yield, and ranking tools
      ├──► Environment Agent: Soiling regression and cleaning detection
      └──► Fault Agent: Zero-gen, status fault, and peer MAD anomaly triage
      │
      ▼
[ Validator ] ──(Verifies tool execution; triggers replan loop if incomplete)
      │
      ▼
[ Synthesizer LLM ] ──(Drafts answer + Automated Number-Grounding Check)
      │
      ▼
[ Final Verified Answer to User ]
```

#### Grounding & Hallucination Guardrail
1. The Synthesizer receives structured JSON outputs from deterministic Python/SQL tools.
2. An automated regex validator scans the drafted response and verifies that every numerical value matches an upstream tool output within a $2\%$ tolerance.
3. If ungrounded metrics are detected, the synthesizer re-drafts the answer. If it fails a second time, it falls back to a deterministic markdown table of verified metrics.

#### Conversational UI Features
- **Session History Sidebar**: Multi-turn chat persistence in SQLite/PostgreSQL with session renaming and deletion.
- **Streaming Response**: Real-time token streaming with formatted markdown tables.
- **Plan & Execution Inspector Drawer**: Inspect the DAG plan, tool arguments, database tables retrieved, and verification logs for complete auditability.

---

## 5. Security & Guardrails

1. **SQL Injection Defense-in-Depth**:
   - `run_sql_readonly()` parses queries with `sqlparse` and enforces `SELECT`-only execution.
   - Denylists destructive commands: `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `TRUNCATE`, `EXEC`.
   - Table allowlisting: Restricted to `plants`, `blocks`, `inverters`, `daily_weather_telemetry`, `daily_inverter_telemetry`.
   - Driver-level event hook (`@event.listens_for(engine, "before_cursor_execute")`) rejects non-SELECT queries at the connection layer.
2. **Query Budget Limiter**: Limits per-session SQL execution to prevent resource exhaustion.
3. **In-Memory Caching**: 60-second TTL cache for all dashboard aggregation endpoints.

---

## 6. Installation & Execution Guide

### 6.1 Prerequisites
- Python 3.12+
- Node.js 18+ & npm
- PostgreSQL 14+ running locally

### 6.2 Environment Configuration
Create a `.env` file in the project root:
```ini
DATABASE_URL=postgresql+psycopg://postgres:123456789@localhost:5432/solardata
CHAT_DATABASE_URL=postgresql+psycopg://postgres:123456789@localhost:5432/solardata

LLM_PROVIDER=groq
GROQ_API_KEY=your_groq_api_key_here
LLM_PLANNER_MODEL=openai/gpt-oss-120b
LLM_AGENT_MODEL=openai/gpt-oss-20b
LLM_SYNTH_MODEL=openai/gpt-oss-120b
LLM_MAX_RPM=20
```

### 6.3 Database Setup & Data Ingestion
Generate and populate the database from July 1, 2026 to October 21, 2026:
```bash
python generate_solar_data.py --start-date 2026-07-01 --end-date 2026-10-21
```

### 6.4 Running the Application

#### Backend (FastAPI)
```bash
# In project root:
uvicorn api.main:app --port 8000 --reload
```

#### Frontend (React / Vite)
```bash
# In frontend/ directory:
npm install
npm run dev
```
Open your browser at `http://localhost:5173`.

### 6.5 Running Automated Tests
```bash
# Run all dashboard endpoint tests:
pytest solar_agent/tests/test_dashboard_endpoints.py -v

# Run fault and peer metrics tests:
pytest solar_agent/tests/test_fault_peer_metrics.py -v

# Run frontend production build validation:
cd frontend && npm run build
```

---

## 7. REST API Reference

| Endpoint | Method | Parameters | Description |
|---|:---:|---|---|
| `/api/meta` | `GET` | — | Returns portfolio metadata (plants, blocks, min/max log dates, LLM settings). |
| `/api/dashboard/overview` | `GET` | `start`, `end`, `agg` | Fleet KPIs, daily generation series, plant comparison table, data quality. |
| `/api/dashboard/plant/{id}` | `GET` | `plant_id`, `start`, `end`, `agg` | Block PRs, best/worst inverters, weather telemetry, treemap hierarchy. |
| `/api/dashboard/plant/{id}/heatmap` | `GET` | `plant_id`, `start`, `end` | Inverter × date PR matrix for heatmap rendering. |
| `/api/dashboard/inverter/{id}` | `GET` | `inverter_id`, `start`, `end`, `agg` | Inverter summary, status timeline, daily PR vs peer median, yield, DC characteristics. |
| `/api/chat` | `POST` | `session_id`, `message`, `model` | Streaming SSE endpoint executing the LangGraph multi-agent pipeline. |
| `/api/sessions` | `GET` | — | Retrieves all stored chat sessions. |
| `/api/sessions` | `POST` | `title` | Creates a new chat session. |
| `/api/sessions/{id}` | `DELETE` | `id` | Deletes a chat session and its message history. |
