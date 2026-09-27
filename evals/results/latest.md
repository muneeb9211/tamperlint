Synthetic corpus, 10 documents per technique, tamperlint 0.1.0.dev0, median 0.22 s per file.

| Technique | Group | Input | SUSPICIOUS | INCONCLUSIVE | INTACT | Main rules |
|---|---|---|---:|---:|---:|---|
| Genuine statement | genuine | pdf | 0/10 | 0/10 | 10/10 | - |
| Genuine invoice | genuine | pdf | 0/10 | 0/10 | 10/10 | - |
| Fast-web-view optimisation | benign-edit | pdf | 0/10 | 0/10 | 10/10 | - |
| Full rewrite by another tool | benign-edit | pdf | 0/10 | 0/10 | 10/10 | - |
| Reviewer comment added | benign-edit | pdf | 0/10 | 0/10 | 10/10 | TL-META-004 |
| Digitally signed | benign-edit | pdf | 0/10 | 0/10 | 10/10 | TL-META-004 |
| Signed, then a comment added | benign-edit | pdf | 0/10 | 0/10 | 10/10 | TL-META-004, TL-SIG-004 |
| Amount covered and retyped (incremental save) | forged | pdf | 10/10 | 0/10 | 0/10 | TL-LOGIC-001, TL-REV-002, TL-FONT-002, TL-OVL-001 |
| As above, balances also corrected | forged | pdf | 10/10 | 0/10 | 0/10 | TL-REV-002, TL-GEO-002, TL-OVL-001, TL-REV-003 |
| Edit markers and editor producer | forged | pdf | 10/10 | 0/10 | 0/10 | TL-EDIT-001, TL-LOGIC-001, TL-REV-002, TL-FONT-002 |
| Edit, then history removed by re-saving | forged | pdf | 10/10 | 0/10 | 0/10 | TL-LOGIC-001, TL-FONT-002, TL-GEO-001, TL-OVL-001 |
| Edited after a digital signature | forged | pdf | 10/10 | 0/10 | 0/10 | TL-LOGIC-001, TL-REV-002, TL-SIG-002, TL-FONT-002 |
| Invoice total inflated at source | forged | pdf | 10/10 | 0/10 | 0/10 | TL-LOGIC-002 |
| Genuine scan (JPEG q70-85) | genuine | scan | 0/10 | 10/10 | 0/10 | - |
| Genuine invoice scan | genuine | scan | 0/10 | 10/10 | 0/10 | - |
| Region pasted from another scan (saved as PNG) | forged | scan | 0/10 | 10/10 | 0/10 | TL-IMG-001 |
| Block of rows copied within the scan | forged | scan | 10/10 | 0/10 | 0/10 | TL-IMG-002 |
| Rebuilt from scratch in the issuer's generator | limitation | pdf | 0/10 | 0/10 | 10/10 | - |

**Forgeries flagged SUSPICIOUS:** 70/80 (88%). **Genuine or benign-edited documents flagged SUSPICIOUS:** 0/90 (0%).

Scans cannot be called INTACT from pixels alone, so genuine scans are reported INCONCLUSIVE by design. The 'rebuilt' row is a known limitation, not a failure of a check.
