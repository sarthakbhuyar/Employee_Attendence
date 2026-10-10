# Review of the starter

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | `health` | It always reports ready even when MongoDB is unreachable. | Stop MongoDB; the runner would receive 200 instead of 503. | Ping MongoDB and return 503 on failure. |
| 2 | `create_employee` | The pre-insert duplicate check races, and no database uniqueness guarantee exists. | Concurrent creates for one code can both return 201. | Added a unique `employees.emp_code` index and map `DuplicateKeyError` to 409. |
| 3 | `create_employee` | `created_at` is a local, naive server time and is returned as a datetime, not epoch milliseconds. | Response violates the OpenAPI type. | Store UTC BSON time (second precision) and serialize all instants at the boundary. |
| 4 | `list_employees` | Pagination skips the first page and totals every employee rather than the filtered set; result order is unspecified. | `page=1` starts at item 21; a department filter shows the wrong total. | Use `(page - 1) * page_size`, filtered count, validation and `emp_code` sort. |
| 5 | `punch_in` | It does not check that the employee exists and its duplicate check races. | Unknown code throws a server error; concurrent requests can duplicate logs. | Return 404 first and rely on a unique `(emp_code,date)` index. |
| 6 | time helpers / `punch_in` | They use the server timezone, ignore IST attendance-day/overnight rules, do not truncate seconds, and calculate late time incorrectly just after grace. | An overnight 01:00 punch is assigned to the wrong date; 09:40:01 should be 10 late minutes. | Centralized IST attendance-date and shift calculations. |
| 7 | `compute_work_hours` / `compute_overtime` | Python float `round` is banker's rounding, overtime has no 30-minute threshold, and overnight end dates are wrong. | A .005 work-hour tie or overnight record yields the wrong result. | Use decimal half-up rounding, thresholded whole minutes and next-day end for overnight shifts. |
| 8 | attendance listing | It loads all matching documents into application memory, sorts there, and has no database pagination. | A 100k-log dataset is slow/high-memory. | Added an index and server-side sort/skip/limit. |
| 9 | attendance responses | `_id` is turned into an undocumented `id`, legacy defaults are not normalized, and BSON datetimes leak into JSON. | OpenAPI response validation fails. | Return only contract fields, epoch milliseconds, empty history and false half-day defaults. |
| 10 | missing contract endpoints | Punch-out, regularization/audit trail, analytics and explain endpoints are absent. | Most documented routes return 404. | Implemented all specified endpoints, atomic updates and aggregation pipelines. |

I did not treat the default shift values as a defect: they agree with the contract. `datetime.now()` itself was not the issue; the issue was using it without UTC/IST conversion and API serialization.
