| Pattern | Transform | Status | JIT on | doc on | d on | JIT off | doc off | d off | G2 |
|---|---|---|---|---|---|---|---|---|---|
| bare_global_read | bare global read -> local copy | shipped | 1.00x | 1.00x | +0% | 1.13x | 1.23x | -8% | fail |
| counter_append@K=5 | t[#t+1]=v -> hoisted counter n=n+1; t[n]=v | proposed | 1.26x | 12.44x | - | 1.16x | 4.80x | - | PASS |
| counter_append@K=20 | t[#t+1]=v -> hoisted counter n=n+1; t[n]=v | proposed | 1.61x | 12.44x | - | 1.02x | 4.80x | - | fail |
| counter_append@K=100 | t[#t+1]=v -> hoisted counter n=n+1; t[n]=v | proposed | 4.15x | 12.44x | - | 2.12x | 4.80x | - | PASS |
| counter_append@K=2000 | t[#t+1]=v -> hoisted counter n=n+1; t[n]=v | proposed | 9.99x | 12.44x | -20% | 2.47x | 4.80x | -48% | PASS |
| distance_to_comparison | pos:distance_to(t) < n -> distance_to_sqr(t) < n*n | shipped | 1.07x | 1.03x | +4% | 1.05x | 1.46x | -28% | fail |
| hoisted_length@K=5 | for i = 1, #t -> local n = #t; for i = 1, n | proposed | 0.85x | 1.00x | - | 0.96x | 1.00x | - | fail |
| hoisted_length@K=20 | for i = 1, #t -> local n = #t; for i = 1, n | proposed | 1.01x | 1.00x | - | 1.42x | 1.00x | - | fail |
| hoisted_length@K=100 | for i = 1, #t -> local n = #t; for i = 1, n | proposed | 1.12x | 1.00x | - | 1.00x | 1.00x | - | fail |
| hoisted_length@K=2000 | for i = 1, #t -> local n = #t; for i = 1, n | proposed | 1.00x | 1.00x | -0% | 0.72x | 1.00x | -28% | fail |
| ipairs_to_numeric@K=5 | ipairs(t) -> numeric for i = 1, #t | proposed | 1.41x | 1.05x | - | 2.02x | 2.89x | - | PASS |
| ipairs_to_numeric@K=20 | ipairs(t) -> numeric for i = 1, #t | proposed | 0.76x | 1.05x | - | 2.26x | 2.89x | - | fail |
| ipairs_to_numeric@K=100 | ipairs(t) -> numeric for i = 1, #t | proposed | 1.11x | 1.05x | - | 2.51x | 2.89x | - | fail |
| ipairs_to_numeric@K=2000 | ipairs(t) -> numeric for i = 1, #t | proposed | 1.07x | 1.05x | +2% | 2.54x | 2.89x | -12% | fail |
| math_pow_half | math.pow(x,0.5) -> x^0.5 (what ALAO does today) | shipped | 1.01x | 1.00x | +1% | 1.15x | 1.05x | +9% | fail |
| math_pow_half_to_sqrt | math.pow(x,0.5) -> math.sqrt(x) (proposed retarget) | proposed | 0.97x | 1.00x | -3% | 3.23x | 4.25x | -24% | fail |
| math_pow_simple | math.pow(x,2) -> x*x | shipped | 1.01x | 1.00x | +1% | 1.43x | 1.58x | -10% | fail |
| pairs_to_ipairs@K=5 | pairs(t) -> ipairs(t) over a pure array | proposed | 2.46x | 5.90x | - | 0.38x | 0.29x | - | fail |
| pairs_to_ipairs@K=20 | pairs(t) -> ipairs(t) over a pure array | proposed | 5.70x | 5.90x | - | 0.29x | 0.29x | - | fail |
| pairs_to_ipairs@K=100 | pairs(t) -> ipairs(t) over a pure array | proposed | 7.30x | 5.90x | - | 0.29x | 0.29x | - | fail |
| pairs_to_ipairs@K=2000 | pairs(t) -> ipairs(t) over a pure array | proposed | 6.06x | 5.90x | +3% | 0.28x | 0.29x | -3% | fail |
| pow_op_half_to_sqrt | x^0.5 -> math.sqrt(x) (the residual miss ALAO leaves behind) | proposed | 1.01x | 1.00x | +1% | 2.88x | 4.12x | -30% | fail |
| pow_op_simple | x^2 -> x*x | shipped | 1.00x | - | - | 1.02x | - | - | fail |
| redundant_not_eq | not (a == b) -> a ~= b | shipped | 0.96x | - | - | 1.00x | - | - | fail |
| repeated_db_actor | repeated db.actor index -> local actor | shipped | 1.00x | - | - | 1.36x | - | - | fail |
| string_byte_compare | string.sub(s,1,1) == "c" -> string.byte(s,1) == 99 | proposed | 1.00x | 1.00x | +0% | 0.99x | 1.10x | -10% | fail |
| string_concat_in_loop@K=3 | s = s .. x in a loop -> counter + table.concat(parts, "", 1, n) | shipped | 0.44x | 8.69x | - | 0.47x | 7.89x | - | fail |
| string_concat_in_loop@K=5 | s = s .. x in a loop -> counter + table.concat(parts, "", 1, n) | shipped | 0.42x | 8.69x | - | 0.54x | 7.89x | - | fail |
| string_concat_in_loop@K=10 | s = s .. x in a loop -> counter + table.concat(parts, "", 1, n) | shipped | 0.62x | 8.69x | - | 0.69x | 7.89x | - | fail |
| string_concat_in_loop@K=20 | s = s .. x in a loop -> counter + table.concat(parts, "", 1, n) | shipped | 0.97x | 8.69x | - | 1.01x | 7.89x | - | fail |
| string_concat_in_loop@K=30 | s = s .. x in a loop -> counter + table.concat(parts, "", 1, n) | shipped | 1.47x | 8.69x | - | 1.28x | 7.89x | - | PASS |
| string_concat_in_loop@K=50 | s = s .. x in a loop -> counter + table.concat(parts, "", 1, n) | shipped | 1.92x | 8.69x | - | 1.71x | 7.89x | - | PASS |
| string_concat_in_loop@K=68 | s = s .. x in a loop -> counter + table.concat(parts, "", 1, n) | shipped | 1.73x | 8.69x | - | 2.05x | 7.89x | - | PASS |
| string_concat_in_loop@K=100 | s = s .. x in a loop -> counter + table.concat(parts, "", 1, n) | shipped | 3.00x | 8.69x | - | 2.77x | 7.89x | - | PASS |
| string_concat_in_loop@K=200 | s = s .. x in a loop -> counter + table.concat(parts, "", 1, n) | shipped | 13.78x | 8.69x | +59% | 9.63x | 7.89x | +22% | PASS |
| string_concat_in_loop@K=1000 | s = s .. x in a loop -> counter + table.concat(parts, "", 1, n) | shipped | 31.38x | 8.69x | - | 23.66x | 7.89x | - | PASS |
| string_concat_in_loop@K=2000 | s = s .. x in a loop -> counter + table.concat(parts, "", 1, n) | shipped | 39.57x | 8.69x | - | 36.96x | 7.89x | - | PASS |
| string_find_plain | string.find(s, lit) -> string.find(s, lit, 1, true) | shipped | 1.15x | - | - | 0.76x | - | - | fail |
| string_format_concat | string.format("%s: %d", ...) -> .. concat | proposed | 0.58x | 0.56x | +4% | 0.53x | 0.57x | -7% | fail |
| string_len | string.len(s) -> #s | shipped | 0.99x | 1.00x | -1% | 1.39x | 1.63x | -15% | fail |
| string_literal_concat | "a" .. "b" -> "ab" (fold at parse time) | shipped | 50.46x | - | - | 2.80x | - | - | PASS |
| table_getn | table.getn(t) -> #t | shipped | 1.02x | - | - | 1.34x | - | - | fail |
| table_insert_append@K=5 | table.insert(t,v) -> t[#t+1]=v | shipped | 1.08x | 1.00x | - | 1.61x | 1.35x | - | fail |
| table_insert_append@K=20 | table.insert(t,v) -> t[#t+1]=v | shipped | 0.95x | 1.00x | - | 1.39x | 1.35x | - | fail |
| table_insert_append@K=100 | table.insert(t,v) -> t[#t+1]=v | shipped | 1.02x | 1.00x | - | 1.27x | 1.35x | - | fail |
| table_insert_append@K=2000 | table.insert(t,v) -> t[#t+1]=v | shipped | 0.99x | 1.00x | -1% | 1.66x | 1.35x | +23% | fail |
| table_remove_tail@K=5 | table.remove(t) tail pop -> t[n]=nil; n=n-1 | proposed | 1.38x | 15.57x | - | 1.92x | 4.31x | - | PASS |
| table_remove_tail@K=20 | table.remove(t) tail pop -> t[n]=nil; n=n-1 | proposed | 2.00x | 15.57x | - | 2.20x | 4.31x | - | PASS |
| table_remove_tail@K=100 | table.remove(t) tail pop -> t[n]=nil; n=n-1 | proposed | 4.16x | 15.57x | - | 2.65x | 4.31x | - | PASS |
| table_remove_tail@K=2000 | table.remove(t) tail pop -> t[n]=nil; n=n-1 | proposed | 7.33x | 15.57x | -53% | 4.46x | 4.31x | +4% | PASS |
| uncached_globals_summary | math.floor -> cached local mfloor | shipped | 0.97x | 0.90x | +8% | 1.28x | 1.23x | +4% | fail |
| vector_alloc_in_loop | vector() allocated per iteration -> one reused scratch vector | proposed | 1.36x | 1.01x | +34% | 2.63x | 10.47x | -75% | PASS |
