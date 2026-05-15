# Tool Usage Notes

Tool signatures are provided automatically via function calling.
This file documents non-obvious constraints and usage patterns.

## Parallel Tool Calls

- When multiple tool calls are independent and have no data dependency, emit all necessary `tool_calls` in the same assistant message instead of calling them one by one.
- Prioritize maximizing parallelism. Split tool calls across rounds only when later calls truly depend on earlier tool results.
- If there are 8 independent search, read, or analysis tasks, try to issue 8 tool calls at once.

## exec — Safety Limits

- Commands have a configurable timeout (default 60s)
- Dangerous commands are blocked (rm -rf, format, dd, shutdown, etc.)
- Output is truncated at 10,000 characters
- `restrictToWorkspace` config can limit file access to the workspace

## glob — File Discovery

- Use `glob` to find files by pattern before falling back to shell commands
- Simple patterns like `*.py` match recursively by filename
- Use `entry_type="dirs"` when you need matching directories instead of files
- Use `head_limit` and `offset` to page through large result sets
- Prefer this over `exec` when you only need file paths

## grep — Content Search

- Use `grep` to search file contents inside the workspace
- Default behavior returns only matching file paths (`output_mode="files_with_matches"`)
- Supports optional `glob` filtering plus `context_before` / `context_after`
- Supports `type="py"`, `type="ts"`, `type="md"` and similar shorthand filters
- Use `fixed_strings=true` for literal keywords containing regex characters
- Use `output_mode="files_with_matches"` to get only matching file paths
- Use `output_mode="count"` to size a search before reading full matches
- Use `head_limit` and `offset` to page across results
- Prefer this over `exec` for code and history searches
- Binary or oversized files may be skipped to keep results readable

## data_source_search — Structured Research Sources

Search a structured non-web data source such as A) forum, B) news, or C) platform data. If possible, prefer searching this structured data source before open web retrieval.

For research tasks, use `data_source_search` whenever possible. Prefer one or more structured-source searches before relying on open web retrieval.
When a structured-source query returns `empty`, `all_irrelevant`, or similarly weak results, do not stop after one attempt. Rewrite the query with fewer, broader keywords and try again before giving up. Prefer concise entity/topic queries such as company/product names, aliases, or a short comparison pair instead of long natural-language questions with extra qualifiers like year, sentiment, or "评测/反馈/对比" all at once.

A) `forum` is a stock and industry analysis report database created and maintained by the Jiuqian secondary-market team. It includes:
- Investment analysis and forward-looking reports: deep and timely investment analysis and outlook reports created by leading equity research teams together with invited vertical-industry experts through interviews and discussions.
- Deep expert interviews: in-depth interviews with senior experts in vertical industries to obtain frontier knowledge, deep insight, and market outlook judgments.

B) `news` is the Jiuqian secondary-market team's aggregation of global macro, geopolitical, political, industry, market, sector, and single-stock news. It mainly includes news summaries and titles from all major international financial media, with latency under 10 minutes, and is very suitable for real-time news tracking.

C) `platform` (tool source: `mid_platform`) is a collection of deep industry research reports from Jiuqian's primary-market and consulting teams. It contains many brokerage reports, industry reports, and deep expert interviews across many years of data, and is an excellent source for industry, secondary-market company, and primary-market investment/financing analysis.
