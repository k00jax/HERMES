# HERMES LLM DEPLOYMENT PLAN
Version: 0.1
Status: Strategic Planning
Last Updated: 2026-02-13

---

## OBJECTIVE

Deploy an offline reasoning engine on ODROID-M1S.

---

## CONSTRAINTS

- 8GB RAM
- No GPU
- ARM architecture
- Thermal limits

---

## MODEL TARGET CLASS

- Quantized 7B models
- 4-bit or 5-bit
- CPU optimized

Candidate approaches:
- GGUF models
- llama.cpp style runtime

---

## RAG PIPELINE PLAN

1. Log ingestion
2. Structured event tagging
3. Embedding generation
4. Vector storage
5. Context retrieval
6. Prompt assembly
7. Local inference

---

## USE CASES

- Environmental pattern recognition
- Behavioral summaries
- Survival knowledge queries
- Context-aware suggestions

---

## NON-GOALS

- Cloud dependence
- Constant background inference
- Large 13B+ models on current hardware

---

LLM is enhancement layer.
Not foundational layer.
