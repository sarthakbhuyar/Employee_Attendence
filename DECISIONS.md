# Decisions

1. **Indexes.** `employees.emp_code` is unique for the business identifier. `employees(department, joined_on)` supports department headcount filtering. `attendance_logs(emp_code, date)` is unique and supports employee-month reads; `attendance_logs(date desc, emp_code)` supports listing. I also added an employee/month metrics index. I rejected indexing `status`: it is low-cardinality and does not lead the important queries.
2. **Punch-in race.** Both requests may verify the employee, then both attempt one insert. The unique `(emp_code, date)` index accepts exactly one document. Its request returns 201; Mongo raises `DuplicateKeyError` for the other and it returns 409. There is no check-then-insert race.
3. **Ties.** The leaderboard uses MongoDB `$rank`, then filters `rank <= limit`. Thus people tied at the cutoff are all returned, in `emp_code` order within equal late totals. This is fair and preserves standard competition ranking.
4. **Headcount.** Department summary starts from eligible employee documents, then left-joins monthly logs. Therefore an employee with no log still contributes one employee code to headcount.
5. **At 100x scale.** I would move reporting to pre-aggregated daily employee and department rollups, updated from an outbox/change stream, while retaining raw logs as the source of truth.
