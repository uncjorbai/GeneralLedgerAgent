# Collaboration request → Financial Close Pipeline (GeneralLedgerGenerator)

**From:** the GL Anomaly Investigator (a separate, downstream product that reacts
to DQ-gate failures in this pipeline).
**To:** whoever maintains `GeneralLedgerGenerator`.
**Type:** small, additive, backward-compatible enhancement. **Not** a redesign.
**Status:** requested — please review and implement in your own style; nothing
here has been changed on your side.

---

## Why this is being asked

A downstream product wakes up when the DQ gate fails and needs to look up *what*
failed by reading the failed run's task output via the Databricks Jobs API. To
do that reliably it must know **which pipeline run failed**.

Today, the only durable artifact written on a failure is the row appended by the
`remediate` task to `gold.remediation_log`, and that row does **not** carry the
Databricks **run id**. Without it, the downstream product has to *guess* the run
("the most recent failed run of the job") — which is safe only because the job
is `max_concurrent_runs=1`, and would break the moment concurrency changes.

**The ask:** stamp the run id onto the `remediation_log` row so the correlation
is exact and future-proof. That's the whole request.

---

## The need (acceptance criteria)

1. Every row `remediate` appends to `gold.remediation_log` includes the
   Databricks **job run id** of the run that failed.
2. The change is backward-compatible: `remediate.py` still runs if invoked
   outside a job (e.g. ad-hoc), with the run id simply blank/null.
3. No behavioral change to the gate, the branch logic, or anything else.

---

## Suggested implementation

Two files. Adjust to your conventions — the sample is illustrative.

### 1. `notebooks/remediate.py` — read the run id, add the column

Add a widget (the job will populate it), and extend the logged row + schema from
three fields to four:

```python
# --- with the other widgets, after the existing dbutils.widgets.text(...) calls ---
dbutils.widgets.text("job_run_id", "")            # populated by the job via {{job.run_id}}
job_run_id = dbutils.widgets.get("job_run_id")

# --- the logged row + schema (currently 3 fields) becomes 4 ---
row = [(dt.datetime.now(), scenario, job_run_id,
        "DQ gate failed - close halted before Silver. Fix source/mapping, "
        "re-land Bronze, and re-run the gate (Step 3 -> Step 2 reprocess).")]
log = spark.createDataFrame(
    row, "logged_at timestamp, scenario string, job_run_id string, action string")
(log.write.mode("append").saveAsTable(f"{catalog}.{gold}.remediation_log"))
```

`job_run_id` defaults to `""`, so an ad-hoc run of the notebook still works.

### 2. `workflow/create_job.py` — feed the run id into that widget

The `remediate` task currently uses the shared `nb()` helper, which builds a
bare `NotebookTask`. Give it its own `NotebookTask` with `base_parameters` so the
platform substitutes the run id at execution time:

```python
jobs.Task(
    task_key="remediate",
    notebook_task=jobs.NotebookTask(
        notebook_path=f"{wsdir}/remediate",
        base_parameters={"job_run_id": "{{job.run_id}}"},
    ),
    depends_on=dep("dq_gate"),
    run_if=jobs.RunIf.AT_LEAST_ONE_FAILED,
),
```

`{{job.run_id}}` is a Databricks built-in dynamic value reference; the platform
replaces it with the actual run id when the task runs.

---

## Outcomes / how to verify

- **Schema:** `DESCRIBE fin_close.gold.remediation_log` shows a new
  `job_run_id string` column (trailing; existing rows read back null).
- **Deploy:** re-run `python workflow/create_job.py` so the task parameter takes
  effect. (Delta `append` tolerates the added trailing column.)
- **Functional test:** reseed any defect scenario so the gate fails, let
  `remediate` run, then:

  ```sql
  SELECT logged_at, scenario, job_run_id
  FROM fin_close.gold.remediation_log
  ORDER BY logged_at DESC LIMIT 1;
  ```

  `job_run_id` should equal the failed run's id (visible in the Jobs UI /
  `jobs.list_runs`). An ad-hoc notebook run should still succeed with
  `job_run_id` blank.

---

## Explicitly NOT part of this request

To keep the pipeline agnostic, the following are **not** being asked for now.
They are noted only so you know the downstream product is *not* expecting them
yet, and will be raised separately (with rationale) if/when needed:

- Persisting the gate's structured verdict (`results[]`) or the failing rows
  (`details{}`) to a table.
- Emitting run context (seed/period/entities).
- Any change to how reconciliation checks behave.
- Anything touching Silver/Gold logic, the branch conditions, or the answer key.

If any of the above would actually be *easier* or cleaner to add on your side,
flag it back — but the only thing needed today is the run id on the log row.
