# ADR-009: Gradient Cascade Memory Architecture

**Date:** 2026-08-22
**Status:** Accepted
**Supersedes:** ADR-004 (Hot/Cold Memory)
**Author:** User (Architect) + Tech Lead (Implementation)

---

## Context

The current memory subsystem (`memory/manager.py` with Hot/Cold tiers) has the following limitations:

1. **No dialogue history.** Only the last turn is stored (`dialogue:last`), overwritten on each new request.
2. **Fixed time-based TTL.** Hot (72h) and Cold (27d) don't adapt to usage patterns.
3. **No priority system.** Important topics can be evicted; unimportant ones can linger.
4. **No protection.** No mechanism to "pin" important topics or prevent accidental deletion.
5. **No command support.** User cannot explicitly forget a topic or clear memory.
6. **No RAG-friendly format.** Raw JSON is passed to LLM, wasting VRAM and tokens.

These limitations are critical for an agent that works in **intermittent (session-based) mode** (not 24/7).

---

## Decision

We will replace Hot/Cold memory with a **Gradient Cascade Memory** architecture.

### Core Principles

1. **Decay is tied to user actions, not system time.**
   - 1 tick = 1 message (user + assistant) in the current session.
   - Decay is applied per message, not per hour.

2. **Score range: -50.0 to +60.0.**
   - `+60.0` is the maximum heat (4 days of protection after inactivity).
   - `-50.0` is the deletion threshold.

3. **Three zones based on score:**
   - **TGS (Term Group Shield):** `score > 50.0` — absolute protection, raw data.
   - **HOT:** `0.1..50.0` — active cache, raw data.
   - **COLD:** `0.0..-49.9` — compressed archive, LLM-generated summaries only.

4. **Three storage files (atomic write):**
   - `data/tgs_memory.json`
   - `data/tg_hot_memory.json`
   - `data/tg_cold_memory.json`

### Heating (Score Increase)

| Action | Increase |
|--------|----------|
| `recall` (read) | `+5.0` |
| `remember` (write/update) | `+10.0` |

Initial score:
- Simple query → `25.0`
- Complex query → `40.0`

### Cooling (Score Decrease)

| Trigger | Decrease | Affected |
|---------|----------|----------|
| Per tick (`DECAY_ACTUAL`) | `max(0.01, 0.1 - (requests * 0.0003))` | All except TGS |
| Session close / restart | `-2.0` | All except TGS |

**Floating Decay:** At high session load (300+ requests), decay slows to `0.01` per tick to preserve context.

### Compression (Hot → Cold)

- **Trigger range:** `5.0..-4.0` (score between 5.0 and -4.0)
- **Action:** `LLMCompressor` creates a summary.
- **Result:** `score = -5.0`, `is_cold = True`, stored in `tg_cold.json`.
- **Reuse:** If a summary already exists, it's reused (no new LLM call).

### Protected Flag

- **Trigger:** `recall` from Cold → `protected = True`, `score = 10.0`
- **Effect:** Prevents compression (even in the 5..-4 range).
- **Removal:** Only when `remember` (mutation) occurs AND `score >= 8.0`.
- **Note:** Does NOT prevent cooling (score still decays).

### Shield Flag (TGS Only)

- **Trigger:** `score > 50.0` → `shield = True`, promoted to TGS.
- **Effect:** Absolute protection from:
  - Deletion (score cannot drop below 50.0)
  - Cooling on session restart (`-2.0` is ignored)
- **Removal:** When `score <= 50.0` (demoted to HOT).

### User Commands

| Command | Effect |
|---------|--------|
| `забудь <topic>` | Apply `-50.0` penalty (or `-20.0` if in TGS) → force into COLD or deletion |
| `забудь всё` | Double-confirmation required. Rotate: TGS→HOT, HOT→COLD, COLD→deletion |

### RAG Context Format (for LLM)

**Strictly forbidden:** Raw JSON.
**Required:** Flat Markdown format:
[TGS: topic_name] description or full log
[HOT: topic_name] description or full log
[COLD: topic_name] LLM-generated summary

This saves VRAM and tokens.

---

## Consequences

### Positive

1. **Dialogue history is preserved.** Each turn is stored as a separate key with its own score.
2. **Important topics are protected.** TGS and Shield prevent accidental eviction.
3. **Context persists across sessions.** Cooling is tied to actions, not time.
4. **High-load sessions keep context.** Floating Decay slows cooling during intensive use.
5. **User control.** `забудь` and `забудь всё` give explicit memory management.
6. **VRAM-efficient RAG.** Markdown format reduces token usage.

### Negative

1. **Increased complexity.** New logic with score, zones, protected, shield, atomic write.
2. **Migration required.** Existing `hot_memory.json` and `cold_memory.json` must be migrated or reset.
3. **Testing effort.** 13 test groups required to cover all scenarios.

### Mitigations

1. **Migration path:** Old files are ignored; new memory starts fresh.
2. **Testing:** Full test suite to be written before merging.
3. **Documentation:** This ADR and updated README.

---

## Rationale

The decision was driven by:

1. **User requirement:** Agent must work in intermittent (session-based) mode, not 24/7.
2. **Observed issue:** Memory was overwriting instead of appending.
3. **User requirement:** No manual commands for memory cleanup.
4. **User requirement:** RAG context must be token-efficient.

The gradient approach was chosen over fixed TTL because it adapts to actual usage patterns and gives users control without requiring them to understand time-based systems.

---

## Related ADRs

- **ADR-004:** Hot/Cold Memory (superseded)
- **ADR-005:** Internal timers (PeriodicScheduler → will be replaced by AdaptiveScheduler)

---

## Approval

- **Architect (User):** Approved
- **Tech Lead (Assistant):** Approved
- **Status:** Accepted for implementation

---

## Implementation Plan

1. ✅ Create `memory/manager.py` with GradientMemory class.
2. ✅ Update `core/agent.py` to use GradientMemory.
3. Update `core/react_loop.py` (remove old memory logic).
4. Write tests (`tests/test_gradient_memory.py`).
5. Run integration tests.
6. Deploy and monitor.
