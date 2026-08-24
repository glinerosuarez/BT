# Candidate Experience Vault: Gabriel Linero

This reference document contains verified factual experiences, metrics, technologies, and achievements for Gabriel Linero. All generated job application responses, essays, and cover letters must draw directly from these grounded details without inventing credentials or hallucinating past experiences.

---

## 1. Candidate Overview & Identity

- **Name**: Gabriel Linero
- **Email**: `linero@usc.edu`
- **Phone**: `+1 (213) 774-1818`
- **Location**: Los Angeles, CA
- **LinkedIn**: [linkedin.com/in/glinerosuarez](https://www.linkedin.com/in/glinerosuarez)
- **GitHub**: [github.com/glinerosuarez](https://github.com/glinerosuarez)
- **Portfolio / Devpost**: [devpost.com/linero](https://devpost.com/linero?ref_content=user-portfolio&ref_feature=portfolio&ref_medium=global-nav)
- **Education**: 
  - **University of Southern California (USC)** — Master of Science in Computer Science
  - **Timeline**: August 2025 – December 2027 (GPA: 3.67 / 4.0)
  - **Key Coursework**: Programming Systems Design (C++, Java), Analysis of Algorithms, Computer Networks, Deep Learning
  - **Leadership**: Director, Society of Hispanic Professional Engineers (SHPE) at USC
- **Work Authorization**: Authorized to work in the US for any employer; no current or future visa sponsorship required for internships/full-time.

---

## 2. Core Work Experience & Projects

### A. Impatico (AI Engineer Intern) — *May 2026 – Present (Los Angeles, CA)*
- **Domain**: Agentic AI platforms, automated document processing, observability, and evaluation infrastructure.
- **Technologies**: Python, PyTorch, LangChain, Vision-Language Models (VLMs), OCR, Spark, SQL, GCP, Docker, Phoenix, OpenTelemetry.
- **Key Achievements & Metrics**:
  - **Document Extraction Pipeline**: Built an automated processing pipeline ingesting data from 1,000+ corporate reports (ESG, financial, and compliance documents). Coordinated orchestration logic, model inference, OCR, and multi-stage data transformations.
  - **Observability & Eval Infrastructure**: Designed and deployed evaluation infrastructure using Phoenix and OpenTelemetry to trace and benchmark model calls, compute precision/recall metrics, and run cross-version regression experiments.
  - **Measurable Result**: Improved system truthfulness and increased extraction precision from **50% (initial zero-shot approach)** to **80% (hybrid RAG & deterministic validation framework)** while bounding latency and compute cost.

### B. EPAM / Baker Hughes (Software Engineer) — *October 2024 – August 2025 (Remote)*
- **Domain**: Cloud backend systems, SaaS platform integrations, industrial IoT data quality, and agentic tool-calling.
- **Technologies**: Python, SQL, Apache Kafka, Apache Spark, Airflow, AWS, Docker, Kubernetes, OpenSearch, Model Context Protocol (MCP).
- **Key Achievements & Metrics**:
  - **Industrial IoT Anomaly Detection**: Built an automated anomaly detection and staging pipeline that intercepted malformed sensor telemetry, firmware schema drifts, and misrouted Kafka topic messages before UI presentation, catching **95% of incorrect IoT records** upstream and preventing corrupt downstream analytics.
  - **Conversational SaaS Integrations**: Developed resilient microservice integrations enabling natural-language conversational queries across complex operational datasets on the Leucipa SaaS platform.
  - **Dynamic Data Tool-Calling**: Implemented tool-calling architectures (including Model Context Protocol) to dynamically query tenant-specific operational stores with full traceability and auditability.

### C. Perficient / American Chemical Society (Software Engineer) — *February 2023 – October 2024 (Remote)*
- **Domain**: Large-scale distributed data engineering, cloud infrastructure cost optimization, and ETL modernization.
- **Technologies**: Python, Java, SQL, Apache Spark, Apache Arrow, Airflow, AWS EMR, Docker, PostgreSQL.
- **Key Achievements & Metrics**:
  - **Spark EMR Optimization**: Diagnosed severe partition data skew, redundant shuffle operations, and PySpark RDD socket serialization bottlenecks (JVM-Python memory duplication) in distributed batch jobs processing multi-terabyte scientific datasets. Adopted Apache Arrow columnar in-memory format for zero-copy data exchange, transitioned logic to DataFrames repartitioned by composite keys, and tuned memory allocation.
  - **Measurable Result**: Reduced batch job runtime from **~24 hours to under 1 hour** (95%+ speedup) and reduced AWS EMR infrastructure spend by **$10,000 per month**.
  - **Framework Modernization**: Built and led team-wide adoption of a modular Python data transformation framework, replacing thousands of lines of legacy Java boilerplate and cutting maintenance overhead.

---

## 3. Technical Core Strengths

- **Languages**: Python (expert), SQL (expert), Java, C++, Go.
- **Data & Distributed Systems**: Apache Spark, Apache Airflow, PostgreSQL, OpenSearch, ETL/ELT pipelines, Kafka/queues, Data Modeling, Anomaly Detection.
- **Cloud & DevOps**: AWS (EMR, S3, EC2, Lambda, IAM), GCP, Docker, Kubernetes, CI/CD, Git, Linux.
- **Observability & AI Rigor**: OpenTelemetry, Phoenix, LangChain, PyTorch, Vision-Language Models, RAG evaluation, Model Context Protocol (MCP), SLA & uptime monitoring.
- **Engineering Methodologies & Workflow**: Spec-Driven Development (SDD), Context Engineering (prompt structuring, retrieval chunking, tool schema curation for agentic systems), Test-Driven Verification, AI-assisted development (Claude Code, Cursor), Schema-First Data Contracts, Atomic Git branching, and Code Review rigor.
