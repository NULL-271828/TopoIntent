# Query-level retrieval differences

Every query is retained in retrieval_query_diff.json with exact field texts, canonical JSON SHA-256, scores and metric contributions. Hash encoding: UTF-8, sorted keys, compact JSON, ensure_ascii=False. No model rerun or post-hoc configuration search.

## multi_field_uniform

8 top-five ranking changes; 4 queries change a Top-k hit; 4 change reciprocal rank.

| Query | Correct | Archived rank | Live rank | Changed metrics |
|---|---|---:|---:|---|
| Education-4_req2 | Education-4 | 1 | 2 | top1, reciprocal_rank |
| Enterprise-5_req2 | Enterprise-5 | 3 | 4 | top3, reciprocal_rank |
| Healthcare-2_req1 | Healthcare-2 | 3 | 4 | top3, reciprocal_rank |
| Healthcare-2_req2 | Healthcare-2 | 2 | 1 | top1, reciprocal_rank |

### Education-4_req2
Query SHA-256: `dc03c33b0b25eee456d3a3ffa5a0371cb70b371a7abd4f8191600498d6f9a109`
Archived: Education-4=0.7925, Education-3=0.7915, Education-1=0.7882, Education-2=0.7762, Healthcare-3=0.6931
Live: Education-3=0.7922, Education-4=0.7921, Education-1=0.7873, Education-2=0.7753, Healthcare-3=0.6934

### Enterprise-5_req2
Query SHA-256: `62c767a843144adce37ccbfa39e57cc288e02583b27d92e09bfabf23f3f56a46`
Archived: Enterprise-3=0.8239, Enterprise-2=0.8107, Enterprise-5=0.8102, Enterprise-4=0.81, Enterprise-1=0.8064
Live: Enterprise-3=0.8232, Enterprise-4=0.8102, Enterprise-2=0.809, Enterprise-5=0.8085, Enterprise-1=0.8064

### Healthcare-2_req1
Query SHA-256: `dc7b6004a91866cbdd693bf11ac673dec017fe58a0a63f22323648b561c91649`
Archived: Healthcare-5=0.8035, Healthcare-3=0.8031, Healthcare-2=0.7763, Healthcare-4=0.775, Healthcare-1=0.75
Live: Healthcare-3=0.8036, Healthcare-5=0.8034, Healthcare-4=0.7749, Healthcare-2=0.7743, Healthcare-1=0.75

### Healthcare-2_req2
Query SHA-256: `7bf9bfc97e44b44f466a42f5003e828a797505853aec2e79be15d442048fbdf6`
Archived: Healthcare-5=0.7999, Healthcare-2=0.7989, Healthcare-3=0.7746, Healthcare-4=0.7693, Healthcare-1=0.7401
Live: Healthcare-2=0.7985, Healthcare-5=0.7984, Healthcare-3=0.7741, Healthcare-4=0.7689, Healthcare-1=0.7393

## multi_field_weighted

15 top-five ranking changes; 3 queries change a Top-k hit; 5 change reciprocal rank.

| Query | Correct | Archived rank | Live rank | Changed metrics |
|---|---|---:|---:|---|
| Enterprise-4_req2 | Enterprise-4 | 2 | 1 | top1, reciprocal_rank |
| Enterprise-5_req1 | Enterprise-5 | 2 | 3 | reciprocal_rank |
| Enterprise-5_req2 | Enterprise-5 | 2 | 3 | reciprocal_rank |
| Healthcare-2_req1 | Healthcare-2 | 3 | 4 | top3, reciprocal_rank |
| Healthcare-2_req2 | Healthcare-2 | 2 | 1 | top1, reciprocal_rank |

### Enterprise-4_req2
Query SHA-256: `3ed6a4e6a8c5b2b25847cdc81535d926ffbafaf544faf62ed0820a8279a49f4d`
Archived: Enterprise-2=0.8597, Enterprise-4=0.8583, Enterprise-1=0.8437, Enterprise-5=0.8393, Enterprise-3=0.8331
Live: Enterprise-4=0.8599, Enterprise-2=0.8589, Enterprise-1=0.8431, Enterprise-5=0.8371, Enterprise-3=0.833

### Healthcare-2_req1
Query SHA-256: `dc7b6004a91866cbdd693bf11ac673dec017fe58a0a63f22323648b561c91649`
Archived: Healthcare-5=0.8446, Healthcare-3=0.8437, Healthcare-2=0.8235, Healthcare-4=0.8234, Healthcare-1=0.8101
Live: Healthcare-5=0.8445, Healthcare-3=0.8442, Healthcare-4=0.8233, Healthcare-2=0.821, Healthcare-1=0.8101

### Healthcare-2_req2
Query SHA-256: `7bf9bfc97e44b44f466a42f5003e828a797505853aec2e79be15d442048fbdf6`
Archived: Healthcare-5=0.8465, Healthcare-2=0.8461, Healthcare-3=0.8238, Healthcare-4=0.8208, Healthcare-1=0.8046
Live: Healthcare-2=0.8456, Healthcare-5=0.8446, Healthcare-3=0.8232, Healthcare-4=0.8202, Healthcare-1=0.8037
