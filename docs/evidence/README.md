# Hackathon Evidence

Artifacts proving tool usage for Devpost judging
(see [office-hours/02-architecture-overview.md](../office-hours/02-architecture-overview.md)
→ CockroachDB tool usage; write-up lands in the root README during Phase 7 / T17).

| Evidence | Status | Location |
|---|---|---|
| **ccloud CLI provisioning screen recording** | ⬜ pending — drop here | `ccloud-provisioning-YYYYMMDD.mp4` (see size note) |
| Distributed vector indexing (runtime) | ✅ in code | [engine/tests/test_vector_canary.py](../../engine/tests/test_vector_canary.py) + `memories` schema (Phase 2) |
| Managed MCP Server sessions (dev-time) | ⬜ collect as they happen | session logs/screenshots in this folder |
| Live deployment (AWS) | ✅ live | URL + verification in [../deploy.md](../deploy.md) |

**Size note for recordings:** GitHub blocks files >100 MB and large binaries bloat the
repo. If the recording is small (≤ ~25 MB compressed), commit it here. Otherwise keep the
original locally, commit a short compressed excerpt or screenshot set here, and upload the
full recording with the submission materials in Phase 7 (link it in this table either way).
