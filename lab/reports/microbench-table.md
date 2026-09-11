| Pattern | Transform | Status | JIT on | doc on | d on | JIT off | doc off | d off | G2 |
|---|---|---|---|---|---|---|---|---|---|
| bare_global_read | bare global read -> local copy | shipped | 0.97x | 1.00x | -3% | 1.08x | 1.23x | -12% | fail |
| counter_append@K=5 | t[#t+1]=v -> hoisted counter n=n+1; t[n]=v | proposed | 0.90x | 12.44x | - | 1.40x | 4.80x | - | fail |
| counter_append@K=20 | t[#t+1]=v -> hoisted counter n=n+1; t[n]=v | proposed | 1.71x | 12.44x | - | 0.94x | 4.80x | - | fail |
| counter_append@K=100 | t[#t+1]=v -> hoisted counter n=n+1; t[n]=v | proposed | 4.34x | 12.44x | - | 2.14x | 4.80x | - | PASS |
| counter_append@K=2000 | t[#t+1]=v -> hoisted counter n=n+1; t[n]=v | proposed | 9.93x | 12.44x | -20% | 2.63x | 4.80x | -45% | PASS |
| distance_to_comparison | pos:distance_to(t) < n -> distance_to_sqr(t) < n*n | shipped | 1.03x | 1.03x | +0% | 1.03x | 1.46x | -29% | fail |
| hoisted_length@K=5 | for i = 1, #t -> local n = #t; for i = 1, n | proposed | 0.99x | 1.00x | - | 0.96x | 1.00x | - | fail |
| hoisted_length@K=20 | for i = 1, #t -> local n = #t; for i = 1, n | proposed | 1.02x | 1.00x | - | 0.99x | 1.00x | - | fail |
| hoisted_length@K=100 | for i = 1, #t -> local n = #t; for i = 1, n | proposed | 1.00x | 1.00x | - | 1.00x | 1.00x | - | fail |
| hoisted_length@K=2000 | for i = 1, #t -> local n = #t; for i = 1, n | proposed | 1.00x | 1.00x | +0% | 1.00x | 1.00x | +0% | fail |
| ipairs_to_numeric@K=5 | ipairs(t) -> numeric for i = 1, #t | proposed | 0.80x | 1.05x | - | 1.93x | 2.89x | - | fail |
| ipairs_to_numeric@K=20 | ipairs(t) -> numeric for i = 1, #t | proposed | 0.73x | 1.05x | - | 2.27x | 2.89x | - | fail |
| ipairs_to_numeric@K=100 | ipairs(t) -> numeric for i = 1, #t | proposed | 1.11x | 1.05x | - | 2.51x | 2.89x | - | fail |
| ipairs_to_numeric@K=2000 | ipairs(t) -> numeric for i = 1, #t | proposed | 1.02x | 1.05x | -3% | 2.63x | 2.89x | -9% | fail |
| math_pow_half | math.pow(x,0.5) -> x^0.5 (what ALAO does today) | shipped | 1.00x | 1.00x | -0% | 1.07x | 1.05x | +2% | fail |
| math_pow_half_to_sqrt | math.pow(x,0.5) -> math.sqrt(x) (proposed retarget) | proposed | 1.00x | 1.00x | -0% | 2.99x | 4.25x | -30% | fail |
| math_pow_simple | math.pow(x,2) -> x*x | shipped | 1.00x | 1.00x | -0% | 1.44x | 1.58x | -9% | fail |
| pairs_to_ipairs@K=5 | pairs(t) -> ipairs(t) over a pure array | proposed | 3.79x | 5.90x | - | 0.38x | 0.29x | - | fail |
| pairs_to_ipairs@K=20 | pairs(t) -> ipairs(t) over a pure array | proposed | 5.75x | 5.90x | - | 0.29x | 0.29x | - | fail |
| pairs_to_ipairs@K=100 | pairs(t) -> ipairs(t) over a pure array | proposed | 7.07x | 5.90x | - | 0.29x | 0.29x | - | fail |
| pairs_to_ipairs@K=2000 | pairs(t) -> ipairs(t) over a pure array | proposed | 6.02x | 5.90x | +2% | 0.28x | 0.29x | -3% | fail |
| pow_op_half_to_sqrt | x^0.5 -> math.sqrt(x) (the residual miss ALAO leaves behind) | proposed | 1.00x | 1.00x | +0% | 2.80x | 4.12x | -32% | fail |
| pow_op_simple | x^2 -> x*x | shipped | 1.00x | - | - | 1.03x | - | - | fail |
| redundant_not_eq | not (a == b) -> a ~= b | shipped | 1.00x | - | - | 1.00x | - | - | fail |
| repeated_db_actor | repeated db.actor index -> local actor | shipped | 1.00x | - | - | 1.35x | - | - | fail |
| string_byte_compare | string.sub(s,1,1) == "c" -> string.byte(s,1) == 99 | proposed | 1.00x | 1.00x | +0% | 0.99x | 1.10x | -10% | fail |
| string_concat_in_loop@K=3 | s = s .. x in a loop -> table.concat | shipped | 0.42x | 8.69x | - | 0.45x | 7.89x | - | fail |
| string_concat_in_loop@K=20 | s = s .. x in a loop -> table.concat | shipped | 0.67x | 8.69x | - | 0.65x | 7.89x | - | fail |
| string_concat_in_loop@K=100 | s = s .. x in a loop -> table.concat | shipped | 1.52x | 8.69x | - | 1.23x | 7.89x | - | PASS |
| string_concat_in_loop@K=2000 | s = s .. x in a loop -> table.concat | shipped | 14.74x | 8.69x | +70% | 12.17x | 7.89x | +54% | PASS |
| string_find_plain | string.find(s, lit) -> string.find(s, lit, 1, true) | shipped | 0.99x | - | - | 1.00x | - | - | fail |
| string_format_concat | string.format("%s: %d", ...) -> .. concat | proposed | 0.54x | 0.56x | -4% | 0.53x | 0.57x | -7% | fail |
| string_len | string.len(s) -> #s | shipped | 1.00x | 1.00x | +0% | 1.41x | 1.63x | -13% | fail |
| string_literal_concat | "a" .. "b" -> "ab" (fold at parse time) | shipped | 32.14x | - | - | 2.10x | - | - | PASS |
| table_getn | table.getn(t) -> #t | shipped | 1.00x | - | - | 1.23x | - | - | fail |
| table_insert_append@K=5 | table.insert(t,v) -> t[#t+1]=v | shipped | 1.02x | 1.00x | - | 1.41x | 1.35x | - | fail |
| table_insert_append@K=20 | table.insert(t,v) -> t[#t+1]=v | shipped | 0.99x | 1.00x | - | 1.67x | 1.35x | - | fail |
| table_insert_append@K=100 | table.insert(t,v) -> t[#t+1]=v | shipped | 1.03x | 1.00x | - | 1.33x | 1.35x | - | fail |
| table_insert_append@K=2000 | table.insert(t,v) -> t[#t+1]=v | shipped | 0.94x | 1.00x | -6% | 2.07x | 1.35x | +54% | fail |
| table_remove_tail@K=5 | table.remove(t) tail pop -> t[n]=nil; n=n-1 | proposed | 1.13x | 15.57x | - | 1.54x | 4.31x | - | fail |
| table_remove_tail@K=20 | table.remove(t) tail pop -> t[n]=nil; n=n-1 | proposed | 1.55x | 15.57x | - | 1.94x | 4.31x | - | PASS |
| table_remove_tail@K=100 | table.remove(t) tail pop -> t[n]=nil; n=n-1 | proposed | 3.99x | 15.57x | - | 2.54x | 4.31x | - | PASS |
| table_remove_tail@K=2000 | table.remove(t) tail pop -> t[n]=nil; n=n-1 | proposed | 7.85x | 15.57x | -50% | 5.11x | 4.31x | +19% | PASS |
| uncached_globals_summary | math.floor -> cached local mfloor | shipped | 0.88x | 0.90x | -2% | 1.16x | 1.23x | -6% | fail |
| vector_alloc_in_loop | vector() allocated per iteration -> one reused scratch vector | proposed | 1.99x | 1.01x | +97% | 2.88x | 10.47x | -72% | PASS |
