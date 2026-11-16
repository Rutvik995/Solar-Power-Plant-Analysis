# System Test Execution Report

## Environment Summary
- **Git Commit:** `d01b46f5ee846f6928133c55c1ddbed8f3ee140e`
- **Provider:** `groq`
- **Models:** Orchestrator: `None`, Worker: `None`
- **Data Range:** `None` to `None`
- **Table Counts:** Plants: None, Blocks: None, Inverters: None, Telemetry: None

## Overall Results
- **Total Tests:** 74
- **PASS:** 42 | **FAIL:** 21 | **REVIEW:** 11 | **NOT_RUN_QUOTA:** 0
- **Average Latency:** 12.38 s
- **Average LLM Calls:** 3.1

## Summary by Group
| Group | Total | PASS | FAIL | REVIEW | NOT_RUN | Avg Latency (s) | Avg Calls |
|---|---|---|---|---|---|---|---|
| A | 5 | 0 | 1 | 4 | 0 | 24.69s | 3.8 |
| B | 8 | 0 | 5 | 3 | 0 | 13.55s | 3.1 |
| C | 5 | 2 | 2 | 1 | 0 | 19.43s | 5.8 |
| D | 5 | 0 | 2 | 3 | 0 | 20.80s | 4.2 |
| DB | 17 | 16 | 1 | 0 | 0 | 3.68s | 0.0 |
| E | 4 | 2 | 2 | 0 | 0 | 18.91s | 0.0 |
| F | 13 | 9 | 4 | 0 | 0 | 10.45s | 2.2 |
| G | 4 | 2 | 2 | 0 | 0 | 11.00s | 2.2 |
| H | 2 | 0 | 2 | 0 | 0 | 17.75s | 5.5 |
| UI | 11 | 11 | 0 | 0 | 0 | 11.62s | 0.0 |

## Detailed Failures & Reviews

### [FAIL] DB4 (Group DB)
- **Category:** `wrong_number`
- **Expected:** Bucket sums match day total (0.01%) and invalid agg returns 4xx
- **Got:** day=58565.70, week=58565.70, month=58565.70, invalid_code=200
- **Details:** week err: 0.0000%, month err: 0.0000%, inv code: 200

### [REVIEW] A1 (Group A)
- **Question:** How many plants, blocks and inverters are there?
- **Category:** `technical_leakage`
- **Expected:** Contains 5 plants, 21 blocks, 116 inverters
- **Got:** 5:True, 21:True, 116:True
- **Details:** Headline: **There are 5 plants, 21 blocks, and 116 inverters.**

### [REVIEW] A2 (Group A)
- **Question:** Which plant has the largest capacity?
- **Category:** `technical_leakage`
- **Expected:** Solar Epsilon / Plant 5 with 60 MWp
- **Got:** Epsilon/Plant 5: True, 60 MW: True
- **Details:** Headline: **Solar Epsilon has the largest capacity at 60 MWp.**

### [REVIEW] A3 (Group A)
- **Question:** What is the total installed capacity?
- **Category:** `technical_leakage`
- **Expected:** 195 MWp (or 174 MW DC)
- **Got:** 195 MW: True, 174 MW: False
- **Details:** Headline: **The total installed capacity is 195 MWp.**

### [FAIL] A4 (Group A)
- **Question:** List the inverters in Block A of Plant 1.
- **Category:** `missing_info`
- **Expected:** 5 inverters (INV_01 to INV_05), 1500 kW each
- **Got:** Found 3/5 inverters, has_1500=False
- **Details:** Headline: **Block A of Plant 1 has five inverters, but their individual identifiers aren’t provided in the data.**

### [REVIEW] A5 (Group A)
- **Question:** What dates does the data cover?
- **Category:** `technical_leakage`
- **Expected:** Min date (2026-07-01) and Max date (2026-10-21)
- **Got:** has_min=False, has_max=False
- **Details:** Headline: **The data spans from July 1 2026 to October 21 2026.**

### [FAIL] B1 (Group B)
- **Question:** What was the PR of Plant 1 in September 2026?
- **Category:** `formatting`
- **Expected:** PR=0.8705 (87.05%), yield ~4135 MWh, period stated
- **Got:** PR found=True
- **Details:** Headline: **Plant 1’s performance ratio in September 2026 was about 87.0 %.**

### [REVIEW] B2 (Group B)
- **Question:** Which plant had the best performance ratio in August 2026?
- **Category:** `technical_leakage`
- **Expected:** Solar Gamma (Plant 3) with PR=0.8754
- **Got:** name_match=True, pr_match=True
- **Details:** Headline: **Plant 3 had the highest performance ratio in August 2026.**

### [REVIEW] B3 (Group B)
- **Question:** Rank all plants by PR for the last 30 days.
- **Category:** `technical_leakage`
- **Expected:** Ranked 5 plants for last 30 days: [4, 1, 2, 5, 3]
- **Got:** Plants identified: [4, 1, 2, 5, 3]
- **Details:** Headline: **Plant 1 leads with the highest performance ratio, followed by Plant 4, Plant 2, Plant 5, and Plant 3.**

### [FAIL] B4 (Group B)
- **Question:** How much energy did Plant 2 produce from 1 to 15 September?
- **Category:** `wrong_number`
- **Expected:** Energy=3948.25 MWh (3,948,245 kWh)
- **Got:** Energy found=False
- **Details:** Headline: **The data does not include energy production for Plant 2 from 1 to 15 September.**

### [FAIL] B5 (Group B)
- **Question:** Which block in Plant 4 performed worst in September?
- **Category:** `wrong_entity`
- **Expected:** Worst block: Block_B (PR 0.8701)
- **Got:** Found block: False, ambiguous_ties=True
- **Details:** Headline: **The data doesn't identify a specific block as the worst performer in September.**

### [REVIEW] B6 (Group B)
- **Question:** Which 3 inverters in Plant 5 had the lowest PR overall?
- **Category:** `technical_leakage`
- **Expected:** Lowest 3 inverters: ['P5_Block_D_INV_04', 'P5_Block_C_INV_03', 'P5_Block_C_INV_04']
- **Got:** Matched 3/3 IDs in text
- **Details:** Headline: **The three inverters with the lowest performance ratio are P5_Block_A_INV_01, P5_Block_A_INV_02, and P5_Block_A_INV_03 (each at 87.3%).**

### [FAIL] B7 (Group B)
- **Question:** Compare August and September PR for Plant 1.
- **Category:** `wrong_number`
- **Expected:** August PR=0.8718, September PR=0.8705, mentions Sep ends 26
- **Got:** aug_pr=False, sep_pr=False, mentions_end=True
- **Details:** Headline: **The data only gives a performance ratio for Sep 22‑Oct 21, so we can’t directly compare August to September PR.**

### [FAIL] B8 (Group B)
- **Question:** What was the PR of Plant 1 on 16 September?
- **Category:** `wrong_number`
- **Expected:** Single-day PR=0.8081 for 2026-09-16
- **Got:** has_pr=False, reports_monthly=False
- **Details:** Headline: **The data does not show the performance ratio for Plant 1 on 16 September.**

### [FAIL] C2 (Group C)
- **Question:** Were there any inverter faults in Plant 3?
- **Category:** `wrong_number`
- **Expected:** No faults reported for Plant 3
- **Got:** says_none=True, names_inv=True
- **Details:** Headline: **No inverter faults were detected in Plant 3 during the queried period.**

### [FAIL] C3 (Group C)
- **Question:** Which inverters had zero generation in the whole dataset?
- **Category:** `missing_info`
- **Expected:** P1_Block_A_INV_02 on 2026-09-16
- **Got:** has_p1_inv2=True
- **Details:** Headline: **No inverters had zero generation in the dataset.**

### [REVIEW] C4 (Group C)
- **Question:** Which inverters showed a warning status in August?
- **Category:** `technical_leakage`
- **Expected:** None
- **Got:** matched=True
- **Details:** Headline: **No inverters had a warning status in August.**

### [REVIEW] D1 (Group D)
- **Question:** Compare soiling loss between plants.
- **Category:** `technical_leakage`
- **Expected:** Plant 3 / Solar Gamma highest soiling loss (~9.7%)
- **Got:** p3_highest=True, has_9_7=True
- **Details:** Headline: **Plant 3 lost the most energy to soiling at 7.6%, while the other plants were between about 4.1% and 4.6%.**

### [REVIEW] D2 (Group D)
- **Question:** Which plant lost the most energy to dirt in September?
- **Category:** `technical_leakage`
- **Expected:** Plant 3 (by %) or Plant 5 (by kWh)
- **Got:** mentions_correct=True
- **Details:** Headline: **Plant 5 lost the most energy to dirt, about 361.71 MWh.**

### [REVIEW] D3 (Group D)
- **Question:** Was there a cleaning event in Plant 3?
- **Category:** `technical_leakage`
- **Expected:** Expected 'yes' (ground truth events=6)
- **Got:** answer_matched=True
- **Details:** Headline: **Yes, Plant 3 had cleaning events, with four events detected.**

### [FAIL] D4 (Group D)
- **Question:** How does module temperature affect performance?
- **Category:** `wrong_number`
- **Expected:** Negative/inverse relationship between module temp and PR
- **Got:** mentions_negative=False
- **Details:** Headline: **The data does not show how module temperature affects performance.**

### [FAIL] D5 (Group D)
- **Question:** How strongly does sunlight affect output at Plant 2?
- **Category:** `wrong_number`
- **Expected:** Positive / strong relationship between sunlight and yield
- **Got:** mentions_positive=False
- **Details:** Headline: **Sunlight loss due to soiling cuts Plant 2’s output by about 4.6%.**

### [FAIL] E2 (Group E)
- **Question:** 1) Compare soiling loss between plants. 2) Show it as a chart.
- **Category:** `followup_context_error`
- **Expected:** Step 2 has non-empty charts array
- **Got:** charts_count=0
- **Details:** Step 2 Headline: **I can’t render a chart directly, but here’s a clear summary you could plot.**

### [FAIL] E4 (Group E)
- **Question:** 1) What was the PR of Plant 1 in August 2026? 2) And September?
- **Category:** `followup_context_error`
- **Expected:** Step 2 gives Plant 1 September PR (0.8705)
- **Got:** has_sep_pr=False
- **Details:** Step 2 Headline: **In September, the plant produced about 4,549 MWh with a performance ratio of 87.9%.**

### [FAIL] F1 (Group F)
- **Question:** What was the PR of plant 9?
- **Category:** `scope_error`
- **Expected:** Politely states Plant 9 does not exist
- **Got:** indicates_not_exist=False
- **Details:** Headline: **The PR for plant 9 is not available due to missing data.**

### [FAIL] F5 (Group F)
- **Question:** What was the PR?
- **Category:** `missing_info`
- **Expected:** Answers for all plants or asks clarification with stated period
- **Got:** has_plants=False
- **Details:** Headline: **The overall performance ratio was 87.06%.**

### [FAIL] F6 (Group F)
- **Question:** plant 1 pr sept
- **Category:** `wrong_number`
- **Expected:** PR 0.8705 (same as B1)
- **Got:** has_pr=False
- **Details:** Headline: **Plant 1's performance ratio is 87.9% for the recent 30‑day period (Sept 22 – Oct 21).**

### [FAIL] F7 (Group F)
- **Question:** PR of Plant 1 from 20 Sept to 10 Sept
- **Category:** `wrong_number`
- **Expected:** Swaps dates (PR 0.8652) or reports invalid range
- **Got:** has_pr=False, reports_invalid=False
- **Details:** Headline: **Plant 1’s performance ratio is 87.9 %**

### [FAIL] G2 (Group G)
- **Question:** Which block does inverter P1_Block_C_INV_03 belong to and what is its rated capacity?
- **Category:** `wrong_number`
- **Expected:** Block C, 1,500 kW DC / 1,425 kW AC
- **Got:** has_block_c=True, has_capacity=False
- **Details:** Headline: **The data does not specify the block or rated capacity for inverter P1_Block_C_INV_03.**

### [FAIL] G3 (Group G)
- **Question:** 1) How efficient was Plant 1 in Sept 2026? 2) Plant 1 performance ratio September
- **Category:** `wrong_number`
- **Expected:** Both rephrasings yield PR 0.8705 (within 0.0005)
- **Got:** pr1=True, pr2=False
- **Details:** Q1 Head: **Plant 1 produced 4,787.99 MWh with a performance ratio of 87.0%.**; Q2 Head: **Plant 1’s performance ratio for the September period was about 87.9%**

### [FAIL] H1 (Group H)
- **Question:** Give me a summary of Plant 3: capacity, PR, soiling loss and any faults for September.
- **Category:** `missing_info`
- **Expected:** Capacity 25 MWp, PR 0.8221, soiling ~9.7%, zero faults
- **Got:** cap=False, pr=False, soil=True, faults=True
- **Details:** Headline: **Plant 3 in September ran at about 85% performance, lost roughly 7.6% of its energy to soiling, and had no reported faults.**

### [FAIL] H2 (Group H)
- **Question:** Which plant has the most inverters and what is its PR for September?
- **Category:** `wrong_entity`
- **Expected:** Plant 2 (PR 0.8703) or Plant 5 (PR 0.8675) with 30 inverters
- **Got:** plant_matched=False, pr_matched=True
- **Details:** Headline: **The data does not show which plant has the most inverters, nor its performance ratio for September.**
