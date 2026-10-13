---
type: "Reference"
title: "Evaluation sets for the legal knowledge base"
description: "Benchmarks for testing whether retrieval + generation over this bundle answers legal questions correctly."
tags: ["eval", "benchmark", "legalbench"]
---

# LegalBench

[LegalBench](legalbench/README.md) — 162 legal-reasoning tasks (issue spotting, rule recall, rule application,
interpretation, rhetorical understanding) collected by Stanford HazyResearch and 40 contributors.
CC BY 4.0. Each task is a folder under `legalbench/data/<task>/` with `train.tsv` (few-shot examples)
and `test.tsv`; `task_metadata.json` describes every task.

Suggested use: run the rule-recall and statutory-interpretation tasks with and without retrieval from
this bundle and compare scores — that measures what the corpus adds.

Source: https://huggingface.co/datasets/nguha/legalbench · paper: https://arxiv.org/abs/2308.11462
