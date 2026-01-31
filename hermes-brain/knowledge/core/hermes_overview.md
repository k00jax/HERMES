# HERMES Overview

HERMES is an offline-first wearable instrument and assistant.

Core principles:
- Works without internet
- Prioritizes environmental awareness
- Stores critical human knowledge locally
- Uses optional internet augmentation only when available

Architecture:
- XIAO microcontroller: always-on sensing and UI
- ODROID SBC: reasoning, retrieval, and language processing

Modes:
- Sentinel mode: low power monitoring
- Scan mode: active sensing and summaries
- Brain mode: deep reasoning using local knowledge
