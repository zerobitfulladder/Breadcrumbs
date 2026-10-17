Passage retrieval on 33 questions with evidence, each asked as written and paraphrased (chunker blocks-1100/v2, embedder BAAI/bge-small-en-v1.5)

| asked | scope | mode | n | hit@1 | hit@3 | hit@5 | hit@10 | MRR |
|---|---|---|---|---|---|---|---|---|
| as written | paper | keyword | 27 | 74% | 93% | 96% | 100% | 0.83 |
| as written | paper | semantic | 27 | 63% | 85% | 89% | 93% | 0.75 |
| as written | paper | hybrid | 27 | 67% | 81% | 89% | 96% | 0.76 |
| as written | library | keyword | 6 | 100% | 100% | 100% | 100% | 1.00 |
| as written | library | semantic | 6 | 83% | 100% | 100% | 100% | 0.92 |
| as written | library | hybrid | 6 | 100% | 100% | 100% | 100% | 1.00 |
| paraphrased | paper | keyword | 27 | 33% | 74% | 78% | 89% | 0.54 |
| paraphrased | paper | semantic | 27 | 33% | 70% | 85% | 85% | 0.53 |
| paraphrased | paper | hybrid | 27 | 37% | 70% | 85% | 85% | 0.57 |
| paraphrased | library | keyword | 6 | 67% | 83% | 100% | 100% | 0.79 |
| paraphrased | library | semantic | 6 | 67% | 83% | 83% | 100% | 0.74 |
| paraphrased | library | hybrid | 6 | 100% | 100% | 100% | 100% | 1.00 |

Paper scope: a passage from an evidence page, searching that paper. Library scope: a passage from the right paper, searching everything.
