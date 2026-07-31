# Session transcript — knowledge: how embedding models work

User: Explain how sentence embedding models produce a vector.

Assistant: A sentence embedding model (e.g. BAAI/bge-small) encodes text into a fixed-dim float vector (typically 384 or 768 dims) such that semantically similar texts land near each other in cosine space.

The pipeline: tokenize → transformer encoder (BERT-style) → mean-pool the final hidden states → L2-normalize. Cosine similarity between two normalized vectors is then just their dot product.

Key gotcha: the mean-pool step MUST match what the model was trained with. fastembed handles this automatically, but if you hand-roll the ONNX call and skip pooling you get garbage vectors that still look plausible (norm near 1, but similarity scores all clustered around 0.5).

Quantization: many GGUF/ONNX models ship quantized to int8 or fp16. For retrieval this is usually fine — recall drops <2% — but for clustering or contradiction detection the higher precision matters.

User: What dimension do I pick?

Assistant: Match the model. bge-small is 384, bge-base is 768, nomic-embed is 768. Storing the wrong dim in sqlite-vec silently corrupts the index because vec_distance_cosine interprets the blob as a different count of floats.
