---
id: sprint-1
title: Core MVP Definition
participants: [cpo, cto, cdo, cco]
prerequisite_files: []
debate_rounds: 2
approval_quorum: 2
---
# Sprint 1: Core MVP Definition

## Core Product Idea
{{CORE_PRODUCT_IDEA}}

Founding sprint. All four spoke roles draft their domain's view of the
baseline cross-functional product roadmap on parallel feature branches,
then critique each other across two debate rounds. The CEO records the
final ADR after the human gate, and then authors the blueprint for the
next sprint from the open questions left in the debate.

## Product Context
We are defining the v1 of the product described above. Each spoke
brings its domain to the table:

1. The CPO frames the user problem, the personas, the jobs-to-be-done,
   the UX surfaces that prove them, and the success metrics.
2. The CTO defines the service topology, external integrations, data
   stores, latency / reliability budgets, on-call surface, and cost
   envelope.
3. The CDO defines the canonical domain entities, the upstream
   ingestion plan, the dedup / lineage model, the retention TTLs, and
   the quality SLOs.
4. The CCO defines the regulatory envelope, the jurisdictional scope,
   the privacy posture, the user-facing disclosures, and the consent
   model.

Every spoke's deliverable must take this problem seriously and refuse
to design around the parts that are inconvenient (especially the
risk-adjacent and data-quality parts).

## Objective
Produce a coherent first-pass cross-functional product roadmap such
that every C-suite domain has stated its constraints, success metrics,
and anti-goals. The CEO will read this bundle to author the next
sprint, so each artifact must be self-contained on `main`.

## Per-role deliverable
Each role writes one markdown file inside their persona's output directory
(see `.claude/agents/{role}.md`). Recommended file slugs:

- cpo → `product/core_app_prd_definition.md` (the canonical PRD)
- cto → `architecture/core_app_technical_constraints.md`
- cdo → `data/core_app_data_strategy.md`
- cco → `compliance/core_app_regulatory_envelope.md`

The CPO's file is treated as the canonical PRD that downstream sprints
read from `main`. The other three are constraints the PRD must
respect; reviewers should flag any mismatch.

## Mandatory cross-spoke topics
Each role must address — at minimum — its angle on the following, so
the debate has something concrete to bite on:

- **User universe**: who the product is for, who it is not for, how
  that boundary changes over time, who owns additions / removals.
  (CPO defines; CDO operationalises; CCO bounds.)
- **Cooling-off / friction window**: where the user-facing flow must
  slow down for risk, audit, or quality reasons. (CCO defines floor;
  CTO defines technical ceiling; CPO defines user-visible framing.)
- **Quantitative claims**: how any user-facing performance or
  comparative number is computed, displayed, and disclosed. (CDO
  defines methodology; CCO defines disclosures; CPO defines surface
  and frequency.)
- **Anti-goals for v1**: each spoke names the features its domain
  refuses to ship in v1 and why.

## Debate focus
- Round 1: surface contradictions (CPO scope vs. CCO risk envelope;
  CTO latency budget vs. CCO cooling-off floor; CDO ingestion SLO vs.
  CTO cost envelope).
- Round 2: confirm resolutions or escalate to the human gate. Any
  unresolved CCO veto must be either accepted (and the PRD scope
  trimmed) or escalated — not deferred.
