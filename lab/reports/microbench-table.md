| Pattern | Transform | Status | JIT on | doc on | d on | JIT off | doc off | d off | G2 |
|---|---|---|---|---|---|---|---|---|---|
| bare_global_read | bare global read -> local copy | shipped | 1.00x | 1.00x | -0% | 1.12x | 1.23x | -9% | fail |
| counter_append@K=5 | t[#t+1]=v -> hoisted counter n=n+1; t[n]=v | proposed | 1.07x | 12.44x | - | 1.05x | 4.80x | - | fail |
| counter_append@K=20 | t[#t+1]=v -> hoisted counter n=n+1; t[n]=v | proposed | 1.61x | 12.44x | - | 1.37x | 4.80x | - | PASS |
| counter_append@K=100 | t[#t+1]=v -> hoisted counter n=n+1; t[n]=v | proposed | 4.22x | 12.44x | - | 2.10x | 4.80x | - | PASS |
| counter_append@K=2000 | t[#t+1]=v -> hoisted counter n=n+1; t[n]=v | proposed | 9.80x | 12.44x | -21% | 2.61x | 4.80x | -46% | PASS |
| distance_to_comparison | pos:distance_to(t) < n -> distance_to_sqr(t) < n*n | shipped | 1.03x | 1.03x | +0% | 1.05x | 1.46x | -28% | fail |
| hoisted_length@K=5 | for i = 1, #t -> local n = #t; for i = 1, n | proposed | 1.00x | 1.00x | - | 0.96x | 1.00x | - | fail |
| hoisted_length@K=20 | for i = 1, #t -> local n = #t; for i = 1, n | proposed | 1.01x | 1.00x | - | 0.98x | 1.00x | - | fail |
| hoisted_length@K=100 | for i = 1, #t -> local n = #t; for i = 1, n | proposed | 1.00x | 1.00x | - | 1.00x | 1.00x | - | fail |
| hoisted_length@K=2000 | for i = 1, #t -> local n = #t; for i = 1, n | proposed | 1.00x | 1.00x | +0% | 1.00x | 1.00x | -0% | fail |
| ipairs_to_numeric@K=5 | ipairs(t) -> numeric for i = 1, #t | proposed | 0.80x | 1.05x | - | 1.94x | 2.89x | - | fail |
| ipairs_to_numeric@K=20 | ipairs(t) -> numeric for i = 1, #t | proposed | 0.73x | 1.05x | - | 2.24x | 2.89x | - | fail |
| ipairs_to_numeric@K=100 | ipairs(t) -> numeric for i = 1, #t | proposed | 1.09x | 1.05x | - | 2.43x | 2.89x | - | fail |
| ipairs_to_numeric@K=2000 | ipairs(t) -> numeric for i = 1, #t | proposed | 1.01x | 1.05x | -3% | 2.63x | 2.89x | -9% | fail |
| math_pow_half | math.pow(x,0.5) -> x^0.5 (what ALAO does today) | shipped | 1.00x | 1.00x | +0% | 1.06x | 1.05x | +1% | fail |
| math_pow_half_to_sqrt | math.pow(x,0.5) -> math.sqrt(x) (proposed retarget) | proposed | 1.00x | 1.00x | +0% | 2.99x | 4.25x | -30% | fail |
| math_pow_simple | math.pow(x,2) -> x*x | shipped | 1.00x | 1.00x | +0% | 1.44x | 1.58x | -9% | fail |
| pairs_to_ipairs@K=5 | pairs(t) -> ipairs(t) over a pure array | proposed | 3.79x | 5.90x | - | 0.38x | 0.29x | - | fail |
| pairs_to_ipairs@K=20 | pairs(t) -> ipairs(t) over a pure array | proposed | 5.73x | 5.90x | - | 0.29x | 0.29x | - | fail |
| pairs_to_ipairs@K=100 | pairs(t) -> ipairs(t) over a pure array | proposed | 7.23x | 5.90x | - | 0.29x | 0.29x | - | fail |
| pairs_to_ipairs@K=2000 | pairs(t) -> ipairs(t) over a pure array | proposed | 6.02x | 5.90x | +2% | 0.28x | 0.29x | -2% | fail |
| pow_op_half_to_sqrt | x^0.5 -> math.sqrt(x) (the residual miss ALAO leaves behind) | proposed | 1.00x | 1.00x | -0% | 2.81x | 4.12x | -32% | fail |
| pow_op_simple | x^2 -> x*x | shipped | 1.00x | - | - | 1.02x | - | - | fail |
| redundant_not_eq | not (a == b) -> a ~= b | shipped | 1.01x | - | - | 1.00x | - | - | fail |
| repeated_db_actor | repeated db.actor index -> local actor | shipped | 1.00x | - | - | 1.35x | - | - | fail |
| string_byte_compare | string.sub(s,1,1) == "c" -> string.byte(s,1) == 99 | proposed | 1.01x | 1.00x | +1% | 0.99x | 1.10x | -10% | fail |
| string_concat_in_loop@K=3 | s = s .. x in a loop -> counter + table.concat(parts, "", 1, n) | shipped | 0.45x | 8.69x | - | 0.47x | 7.89x | - | fail |
| string_concat_in_loop@K=5 | s = s .. x in a loop -> counter + table.concat(parts, "", 1, n) | shipped | 0.48x | 8.69x | - | 0.51x | 7.89x | - | fail |
| string_concat_in_loop@K=10 | s = s .. x in a loop -> counter + table.concat(parts, "", 1, n) | shipped | 0.65x | 8.69x | - | 0.70x | 7.89x | - | fail |
| string_concat_in_loop@K=20 | s = s .. x in a loop -> counter + table.concat(parts, "", 1, n) | shipped | 0.98x | 8.69x | - | 0.93x | 7.89x | - | fail |
| string_concat_in_loop@K=30 | s = s .. x in a loop -> counter + table.concat(parts, "", 1, n) | shipped | 1.38x | 8.69x | - | 1.29x | 7.89x | - | PASS |
| string_concat_in_loop@K=50 | s = s .. x in a loop -> counter + table.concat(parts, "", 1, n) | shipped | 2.00x | 8.69x | - | 1.67x | 7.89x | - | PASS |
| string_concat_in_loop@K=68 | s = s .. x in a loop -> counter + table.concat(parts, "", 1, n) | shipped | 2.22x | 8.69x | - | 1.78x | 7.89x | - | PASS |
| string_concat_in_loop@K=100 | s = s .. x in a loop -> counter + table.concat(parts, "", 1, n) | shipped | 3.15x | 8.69x | - | 2.59x | 7.89x | - | PASS |
| string_concat_in_loop@K=200 | s = s .. x in a loop -> counter + table.concat(parts, "", 1, n) | shipped | 9.14x | 8.69x | +5% | 8.58x | 7.89x | +9% | PASS |
| string_concat_in_loop@K=1000 | s = s .. x in a loop -> counter + table.concat(parts, "", 1, n) | shipped | 24.67x | 8.69x | - | 20.03x | 7.89x | - | PASS |
| string_concat_in_loop@K=2000 | s = s .. x in a loop -> counter + table.concat(parts, "", 1, n) | shipped | 36.63x | 8.69x | - | 28.88x | 7.89x | - | PASS |
| string_find_plain | string.find(s, lit) -> string.find(s, lit, 1, true) | shipped | 1.00x | - | - | 1.00x | - | - | fail |
| string_format_concat | string.format("%s: %d", ...) -> .. concat | proposed | 0.54x | 0.56x | -3% | 0.54x | 0.57x | -5% | fail |
| string_len | string.len(s) -> #s | shipped | 1.00x | 1.00x | +0% | 1.39x | 1.63x | -15% | fail |
| string_literal_concat | "a" .. "b" -> "ab" (fold at parse time) | shipped | 32.22x | - | - | 2.46x | - | - | PASS |
| table_getn | table.getn(t) -> #t | shipped | 1.00x | - | - | 1.35x | - | - | fail |
| table_insert_append@K=5 | table.insert(t,v) -> t[#t+1]=v | shipped | 1.05x | 1.00x | - | 1.33x | 1.35x | - | fail |
| table_insert_append@K=20 | table.insert(t,v) -> t[#t+1]=v | shipped | 0.91x | 1.00x | - | 1.74x | 1.35x | - | fail |
| table_insert_append@K=100 | table.insert(t,v) -> t[#t+1]=v | shipped | 1.02x | 1.00x | - | 1.25x | 1.35x | - | fail |
| table_insert_append@K=2000 | table.insert(t,v) -> t[#t+1]=v | shipped | 1.02x | 1.00x | +2% | 1.65x | 1.35x | +22% | fail |
| table_remove_tail@K=5 | table.remove(t) tail pop -> t[n]=nil; n=n-1 | proposed | 1.33x | 15.57x | - | 1.89x | 4.31x | - | PASS |
| table_remove_tail@K=20 | table.remove(t) tail pop -> t[n]=nil; n=n-1 | proposed | 1.92x | 15.57x | - | 2.17x | 4.31x | - | PASS |
| table_remove_tail@K=100 | table.remove(t) tail pop -> t[n]=nil; n=n-1 | proposed | 4.31x | 15.57x | - | 2.84x | 4.31x | - | PASS |
| table_remove_tail@K=2000 | table.remove(t) tail pop -> t[n]=nil; n=n-1 | proposed | 7.37x | 15.57x | -53% | 4.53x | 4.31x | +5% | PASS |
| uncached_globals_summary | math.floor -> cached local mfloor | shipped | 1.00x | 0.90x | +11% | 1.27x | 1.23x | +3% | fail |
| vector_alloc_in_loop | vector() allocated per iteration -> one reused scratch vector | proposed | 1.31x | 1.01x | +30% | 2.63x | 10.47x | -75% | PASS |
