---
id: summarize-chunk
version: 1.0.0
syntax: template
variables: [chunk_num, total_chunks, chunk_text]
description: Hierarchical summarization of one chunk of an oversized session transcript (summarize_sessions).
---
Summarize this portion ($chunk_num/$total_chunks) of a coding session transcript in 3-5 sentences, capturing key decisions, errors encountered, and solutions found. Focus on what would be useful to remember in future sessions.

Transcript:
$chunk_text
