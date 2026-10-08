# ☀️ Solar Power Plant Analysis — Multi-Agent System

<p center>
  <img src="https://readme-typing-svg.demolab.com?font=Fira+Code&weight=600&size=24&pause=1000&color=F7B731&center=true&vCenter=true&width=600&lines=Multi-Agent+Solar+Analytics;Powered+by+LangGraph+%26+Gemini+2.5;Deterministic+Numerical+Precision" alt="Typing SVG" />
</p>

---

## 📌 Overview

A **LangGraph-powered multi-agent system** that evaluates solar energy generation efficiency, detects anomalies, and answers natural-language questions about solar power plant performance. 

> 🔒 **Core Design Principle:** *LLMs plan and explain; deterministic tools compute.* Every numerical metric is calculated via SQL and Python execution—zero halluncinated numbers.

---

## 🛠 Tech & Frameworks

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![LangChain](https://img.shields.io/badge/LangGraph-Multi--Agent-000000?style=for-the-badge&logo=langchain&logoColor=white)
![Gemini AI](https://img.shields.io/badge/Gemini_2.5-Pro%2FFlash-8E7CC3?style=for-the-badge&logo=google&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-14+-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)
![Pytest](https://img.shields.io/badge/Testing-Pytest-0A9EDC?style=for-the-badge&logo=pytest&logoColor=white)

---

## 🏗 System Architecture

```text
 User Query
   │
   ▼
[1] Orchestrator / Planner ───► Gemini 2.5 Pro (Generates DAG Plan)
   │
   ▼
[2] DAG Executor (LangGraph) ── Runs parallel/layered execution nodes
   ├── 📊 Data Agent            ─── Gemini 2.5 Flash
   ├── ⚡ Performance Agent     ─── Deterministic Calculation Tools
   ├── 🛠 Fault Diagnosis Agent ─── Deterministic Tools + Reasoning
   └── 🌡 Environmental Agent   ─── Weather & Irradiance Analysis
   │
   ▼
[3] Validator ───────────────► Gemini 2.5 Flash (Grounding Check)
   │
   ▼
[4] Synthesizer ─────────────► Gemini 2.5 Pro (Final Explanation)
