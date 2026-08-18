# Demo persona — six-month body-recomposition story (v1)

Fictional account used to seed the demo user. Written in the Phase 4 conversion grammar
(`cli/convert.py`): §2 carries point events, §3 carries period facts. Nothing here is real
personal data, so unlike the ADR-7 reconstruction this file is committable.

Persona: 24-year-old office worker, desk job, Pune. Six months from a sedentary/junk-food
baseline to a consistent high-protein diet and a four-day-a-week gym routine.

**Rules this file obeys** (see `docs/engineering/replay-architecture.md` §4.1):

- A behaviour that ran over a range lives in §3 **once**. The §2 line that marks the day it
  changed converts to a dated `note` (the converter's change-marker rule), so nothing is
  double-counted.
- Every precise number lives in `demo_payloads.json`, never in this prose.
- The data states *what happened*, never *what caused what*. The causal reading is the
  engine's job to derive, not this file's to assert.

---

## → §2 Timeline

### 2026-01

- **2026-01-05** (exact, scan) — weight: morning weigh-in, gym body-composition scale
- **2026-01-05** (exact, scan) — body-scan: baseline composition scan on the gym's BIA scale
- **2026-01-06** (exact, memory) — habit: desk job, 8–10 hours seated per working day
- **2026-01-08** (exact, memory) — note: ordering fast food 4–5 times a week
- **2026-01-12** (exact, memory) — note: no regular exercise; step count sitting around 3,000–4,000 a day
- **2026-01-15** (exact, memory) — meal-pattern: breakfast is usually tea and biscuits, or skipped entirely
- **2026-01-20** (exact, memory) — meal-pattern: lunch and dinner are mostly rice, fried food, burgers, pizza, noodles or other restaurant food
- **2026-01-25** (exact, memory) — note: protein intake estimated at roughly 50–60 g a day
- **2026-01-31** (exact, scan) — weight: end-of-month weigh-in

### 2026-02

- **2026-02-07** (exact, memory) — note: decided to change how I eat after weeks of low energy and creeping weight
- **2026-02-15** (exact, scan) — weight: mid-month weigh-in
- **2026-02-15** (exact, scan) — body-scan: composition scan on the gym's BIA scale
- **2026-02-16** (exact, memory) — meal-pattern: started replacing one junk-food meal a day with home-cooked food

### 2026-03

- **2026-03-01** (exact, memory) — meal-pattern: started a structured nutrition plan
- **2026-03-05** (exact, memory) — note: eggs, paneer, curd, dal and chicken now part of the regular rotation
- **2026-03-10** (exact, memory) — note: started tracking meals and daily protein
- **2026-03-15** (exact, memory) — note: energy through the working day feels better than it did in January
- **2026-03-16** (exact, scan) — weight: mid-month weigh-in
- **2026-03-16** (exact, scan) — body-scan: composition scan on the gym's BIA scale
- **2026-03-25** (exact, memory) — note: fast food down to roughly once a week
- **2026-03-31** (exact, scan) — weight: end-of-month weigh-in

### 2026-04

- **2026-04-01** (exact, memory) — habit: joined a gym
- **2026-04-08** (exact, memory) — note: normal muscle soreness through the first week of training
- **2026-04-15** (exact, memory) — note: two full weeks of gym training completed without missing a session
- **2026-04-20** (exact, scan) — weight: mid-month weigh-in
- **2026-04-20** (exact, scan) — body-scan: composition scan on the gym's BIA scale
- **2026-04-30** (exact, memory) — note: clothes feeling slightly looser around the waist

### 2026-05

- **2026-05-05** (exact, memory) — note: fast food down to roughly once every two weeks
- **2026-05-12** (exact, memory) — note: lifting noticeably more than in the first month of training
- **2026-05-18** (exact, scan) — weight: mid-month weigh-in
- **2026-05-18** (exact, scan) — body-scan: composition scan on the gym's BIA scale
- **2026-05-25** (exact, memory) — note: held four gym sessions a week through a busy stretch at work
- **2026-05-31** (exact, scan) — weight: end-of-month weigh-in

### 2026-06

- **2026-06-05** (exact, memory) — note: cooking most meals at home now
- **2026-06-10** (exact, memory) — note: sleeping around 7 hours a night, up from the January stretch
- **2026-06-20** (exact, memory) — note: three consecutive months of structured exercise completed
- **2026-06-25** (exact, memory) — note: waist measurement down compared with the January baseline
- **2026-06-28** (exact, memory) — note: skipped the usual month-end weigh-in; next measurement is the six-month one

### 2026-07

- **2026-07-05** (exact, scan) — weight: six-month weigh-in
- **2026-07-05** (exact, scan) — body-scan: six-month composition scan on the gym's BIA scale
- **2026-07-05** (exact, memory) — note: against the January baseline, weight is down 6.6 kg and skeletal muscle is up about 1.2 kg
- **2026-07-06** (exact, memory) — note: high-protein diet and four-day gym routine held for about three months now
- **2026-07-07** (exact, memory) — note: started keeping a sleep log alongside meals and training
- **2026-07-14** (exact, memory) — note: busy stretch at work; ate out twice but kept the rest of the week on plan
- **2026-07-20** (exact, scan) — weight: mid-month weigh-in
- **2026-07-22** (exact, memory) — note: trainer suggested a protein shake on training days to make the daily target easier to hit
- **2026-07-27** (exact, memory) — supplement: started a daily whey protein shake
- **2026-07-28** (exact, memory) — note: moved bedtime earlier so the morning sessions stop eating into sleep

### 2026-08 (current — live logging takes over from 2026-08-19)

- **2026-08-02** (exact, memory) — workout-pattern: added an easy Sunday run to the week
- **2026-08-05** (exact, memory) — note: recovery between sessions feels quicker than it did in the spring
- **2026-08-10** (exact, memory) — note: waist down another notch; jeans from January fit again
- **2026-08-16** (exact, scan) — weight: six-week weigh-in
- **2026-08-16** (exact, scan) — body-scan: composition scan on the gym's BIA scale
- **2026-08-17** (exact, memory) — note: eight months in — the routine no longer takes deciding, it just happens

---

## → §3 Behaviour phases

### Diet phases

- **2026-01-05 → 2026-02-15** (exact, memory) — meal-pattern: sedentary-period eating — fast food 4–5 times a week, breakfast usually skipped or tea and biscuits, roughly 50–60 g protein a day
- **2026-02-16 → 2026-02-28** (exact, memory) — meal-pattern: one home-cooked meal a day replacing a junk-food meal, roughly 70 g protein a day
- **2026-03-01 → 2026-03-31** (exact, memory) — meal-pattern: structured nutrition plan — eggs, paneer, curd, dal and chicken in rotation, fast food down to 1–2 meals a week, roughly 90 g protein a day
- **2026-04-01 → 2026-04-30** (exact, memory) — meal-pattern: high-protein eating alongside the first month of gym training, roughly 110 g protein a day
- **2026-05-01 → 2026-05-31** (exact, memory) — meal-pattern: mostly home-cooked, fast food roughly once a fortnight, roughly 120 g protein a day
- **2026-06-01 → 2026-07-05** (exact, memory) — meal-pattern: settled high-protein routine, most meals cooked at home, roughly 125–130 g protein a day
- **2026-07-06 → 2026-08-18** (exact, memory) — meal-pattern: routine holding — home-cooked meals through the week, a shake on training days from late July, roughly 130 g protein a day

### Training blocks

- **2026-04-02 → 2026-04-15** (exact, memory) — workout-pattern: beginner strength training, about 45 minutes a session — Thursday session of a four-days-a-week block (Mon/Tue/Thu/Sat)
- **2026-04-04 → 2026-04-15** (exact, memory) — workout-pattern: beginner strength training, about 45 minutes a session — Saturday session of a four-days-a-week block (Mon/Tue/Thu/Sat)
- **2026-04-06 → 2026-04-15** (exact, memory) — workout-pattern: beginner strength training, about 45 minutes a session — Monday session of a four-days-a-week block (Mon/Tue/Thu/Sat)
- **2026-04-07 → 2026-04-15** (exact, memory) — workout-pattern: beginner strength training, about 45 minutes a session — Tuesday session of a four-days-a-week block (Mon/Tue/Thu/Sat)
- **2026-04-16 → 2026-04-30** (exact, memory) — workout-pattern: strength training with progressive load increases, about 45 minutes a session — Thursday session of a four-days-a-week block (Mon/Tue/Thu/Sat)
- **2026-04-18 → 2026-04-30** (exact, memory) — workout-pattern: strength training with progressive load increases, about 45 minutes a session — Saturday session of a four-days-a-week block (Mon/Tue/Thu/Sat)
- **2026-04-20 → 2026-04-30** (exact, memory) — workout-pattern: strength training with progressive load increases, about 45 minutes a session — Monday session of a four-days-a-week block (Mon/Tue/Thu/Sat)
- **2026-04-21 → 2026-04-30** (exact, memory) — workout-pattern: strength training with progressive load increases, about 45 minutes a session — Tuesday session of a four-days-a-week block (Mon/Tue/Thu/Sat)
- **2026-05-02 → 2026-05-31** (exact, memory) — workout-pattern: strength training, about 50 minutes a session — Saturday session of a four-days-a-week block (Mon/Tue/Thu/Sat)
- **2026-05-04 → 2026-05-31** (exact, memory) — workout-pattern: strength training, about 50 minutes a session — Monday session of a four-days-a-week block (Mon/Tue/Thu/Sat)
- **2026-05-05 → 2026-05-31** (exact, memory) — workout-pattern: strength training, about 50 minutes a session — Tuesday session of a four-days-a-week block (Mon/Tue/Thu/Sat)
- **2026-05-07 → 2026-05-31** (exact, memory) — workout-pattern: strength training, about 50 minutes a session — Thursday session of a four-days-a-week block (Mon/Tue/Thu/Sat)
- **2026-06-01 → 2026-07-05** (exact, memory) — workout-pattern: progressive strength training, about 55 minutes a session — Monday session of a four-to-five-days-a-week block (Mon/Tue/Thu/Sat)
- **2026-06-02 → 2026-07-05** (exact, memory) — workout-pattern: progressive strength training, about 55 minutes a session — Tuesday session of a four-to-five-days-a-week block (Mon/Tue/Thu/Sat)
- **2026-06-04 → 2026-07-05** (exact, memory) — workout-pattern: progressive strength training, about 55 minutes a session — Thursday session of a four-to-five-days-a-week block (Mon/Tue/Thu/Sat)
- **2026-06-06 → 2026-07-05** (exact, memory) — workout-pattern: progressive strength training, about 55 minutes a session — Saturday session of a four-to-five-days-a-week block (Mon/Tue/Thu/Sat)
- **2026-07-06 → 2026-08-18** (exact, memory) — workout-pattern: progressive strength training, about 55 minutes a session — Monday session of a four-days-a-week block (Mon/Tue/Thu/Sat)
- **2026-07-07 → 2026-08-18** (exact, memory) — workout-pattern: progressive strength training, about 55 minutes a session — Tuesday session of a four-days-a-week block (Mon/Tue/Thu/Sat)
- **2026-07-09 → 2026-08-18** (exact, memory) — workout-pattern: progressive strength training, about 55 minutes a session — Thursday session of a four-days-a-week block (Mon/Tue/Thu/Sat)
- **2026-07-11 → 2026-08-18** (exact, memory) — workout-pattern: progressive strength training, about 55 minutes a session — Saturday session of a four-days-a-week block (Mon/Tue/Thu/Sat)
- **2026-08-02 → 2026-08-18** (exact, memory) — workout-pattern: easy Sunday run, about 30 minutes, added on top of the strength block

### Supplements

- **2026-07-27 → 2026-08-18** (exact, memory) — supplement: one 25 g scoop of whey protein a day, taken after training on gym days

### Sleep

- **2026-07-07 → 2026-07-27** (exact, memory) — sleep-pattern: around 6.9 hours a night, from the first three weeks of the sleep log
- **2026-07-28 → 2026-08-18** (exact, memory) — sleep-pattern: around 7.6 hours a night after moving bedtime earlier

### Activity phases

- **2026-01-05 → 2026-02-15** (exact, memory) — habit: sedentary — around 3,000–4,000 steps a day, no structured exercise
- **2026-02-16 → 2026-02-28** (exact, memory) — habit: around 4,500 steps a day, still no structured exercise
- **2026-03-01 → 2026-03-31** (exact, memory) — habit: around 5,500 steps a day, walking more deliberately
- **2026-04-01 → 2026-04-30** (exact, memory) — habit: around 6,500 steps a day alongside the first month of gym training
- **2026-05-01 → 2026-05-31** (exact, memory) — habit: around 7,000 steps a day
- **2026-06-01 → 2026-07-05** (exact, memory) — habit: around 8,000 steps a day
- **2026-07-06 → 2026-08-18** (exact, memory) — habit: around 8,500 steps a day

---

## Conversion notes (human-only — not parsed)

**Two materialization choices**, both made here so they are reviewable rather than buried in
the JSONL:

1. **"4 days a week" is expressed as four weekly lanes** (Mon/Tue/Thu/Sat). The converter's
   cadence vocabulary is `daily`/`weekly`/`biweekly` — a fixed day-step — so a four-per-week
   pattern is materialized as four weekly period facts, one per weekday. The weekdays
   themselves are a materialization detail the story did not specify; every expanded row
   carries `expanded_from.assertion` naming the real claim ("four days a week") and a lowered
   confidence, so nothing presents an invented Tuesday as an observed one.
2. **June's "4–5 days a week" materializes at four**, not five. Understating an approximate
   assertion is the safe direction; the assertion text keeps the full range.

**Why weigh-ins are `weight` and composition is `body-scan`.** The consolidatable `weight_kg`
series reads `weight` rows, while `body_fat_pct` reads `body_scan` rows
(`engine/retrieval.py` `METRICS`). Splitting them keeps one number in exactly one place: the
scan rows carry composition only, never a second copy of the weight, so no aggregate can
count the same measurement twice.

**Step counts are notes, not a series.** `habit` maps to `note`, and there is no consolidatable
steps metric, so each activity phase is a single background row stating the level — not one
row per day, which would be 180 rows carrying no aggregatable number.
