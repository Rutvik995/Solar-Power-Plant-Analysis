# ☀️ Solar Power Plant Analysis — Multi-Agent System

A **LangGraph-powered multi-agent intelligence platform** for solar photovoltaic (PV) operational telemetry analysis. The system evaluates solar energy generation efficiency, detects anomalies and hardware faults, evaluates dust/soiling losses, and answers natural language questions about solar power plant performance.

> 🔒 **Core Design Axiom**: LLMs plan the DAG and synthesize narrative explanations, while deterministic Python and SQL tools compute all numerical values. Numbers come directly from tool outputs, and an automated grounding check flags mismatches.

---

## 🏗 Architecture Diagram

```text
                           ┌────────────────────────┐
                           │   User Question / UI   │
                           └───────────┬────────────┘
                                       │
                                       ▼
                           ┌────────────────────────┐
                           │      start_node        │  (Assigns unique run_id UUID)
                           └───────────┬────────────┘
                                       │
                                       ▼
                           ┌────────────────────────┐
                 ┌────────►│      Orchestrator      │  (Planner LLM generates DAG Plan)
                 │         └───────────┬────────────┘
                 │                     │
                 │                     ▼
                 │         ┌────────────────────────┐
                 │         │      DAG Executor      │◄─┐
                 │         │  (Topological Layers)  │──┘ (Executes parallel tasks)
                 │         └───────────┬────────────┘
                 │           ├── 📊 Data Agent (Deterministic SQL, Zero-LLM)
                 │           ├── ⚡ Performance Agent (KPIs, PR, Rankings)
                 │           ├── 🛠 Fault Agent (Zero-gen, Hardware Streaks, Peer MAD)
                 │           └── 🌡 Environmental Agent (Soiling, Weather Regression)
                 │                     │ All tasks complete
                 │                     ▼
                 │         ┌────────────────────────┐
          REPLAN │         │       Validator        │  (Status, deps, & metric verification)
          (≤ 2x) │         └───────────┬────────────┘
                 │                     │
                 ├─────────────────────┴────────────┐
                 │                                  │ PASS / FAIL CAP
                 ▼                                  ▼
      [Replan Context]                     ┌─────────────────┐
                                           │   Synthesizer   │  (Narrative synthesis +
                                           └────────┬────────┘   Grounding verification)
                                                    │
                                                    ▼
                                           ┌─────────────────┐
                                           │       END       │  (Verified answer + citations)
                                           └─────────────────┘
```

---

## ⚙️ Setup & Installation

### 1. Prerequisites
- **Python**: 3.11 or 3.12
- **PostgreSQL**: 14+ with read-only user access (or local test database)
- **Groq API Key**: Free API key from [Groq Console](https://console.groq.com)

### 2. Clone and Setup Environment
```bash
# Clone the repository
git clone https://github.com/Rutvik995/Solar-Power-Plant-Analysis.git
cd Solar-Power-Plant-Analysis

# Create and activate virtual environment
python -m venv .venv
# On Windows:
.venv\Scripts\activate
# On Linux/macOS:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Environment Configuration
Copy `.env.example` to `.env` and provide your credentials:
```bash
cp .env.example .env
```
In `.env`:
```env
# Database connection (read-only recommended)
DATABASE_URL=postgresql+psycopg://solar_ro:changeme@localhost:5432/solardata

# Free LLM Provider (Groq)
LLM_PROVIDER=groq
GROQ_API_KEY=gsk_your_groq_api_key_here
LLM_PLANNER_MODEL=llama-3.3-70b-versatile
LLM_AGENT_MODEL=llama-3.1-8b-instant
LLM_SYNTH_MODEL=llama-3.3-70b-versatile
LLM_MAX_RPM=20
```

---

## 🚀 Running the System

### Run the Test Suite (276 Tests)
```bash
pytest
```
*Runs completely offline using deterministic unit tests and recorded fixtures.*

### Run the Evaluation Benchmark
```bash
# Offline replay mode (uses checked-in llm_cache.json, zero API quota)
python scripts/run_eval.py --replay

# Live mode (uses configured Groq API)
python scripts/run_eval.py --live
```

### Launch the Streamlit Interactive UI
```bash
streamlit run app.py
```
*Provides interactive conversational queries, execution DAG visualization, metrics tables, and Plotly charts.*

---

## 💬 6 Example Questions

1. **Overall Performance**: *"What is the total yield across all plants in September 2026?"*
2. **Plant Performance Ratio**: *"What is the Performance Ratio (PR) of Plant 1 in September 2026?"*
3. **Soiling Loss Analysis**: *"How much energy was lost to soiling at Plant 1 in September 2026?"*
4. **Peer Underperformance**: *"Which inverters in Plant 2 underperformed their block peers in September 2026?"*
5. **Fault Diagnostics**: *"Did Plant 3 have any fault events or zero-generation days in September 2026?"*
6. **Weather Correlation**: *"How does ambient temperature affect Plant 1 performance ratio?"*
