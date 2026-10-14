# DECISIONS.md

1. **Indexes.** I created a unique `(emp_code, date)` index on attendance logs to prevent duplicate daily records and support employee/date lookups; a unique `emp_code` index on employees for identity and joins; `(date, late_minutes)` for monthly late-record filtering; `(date descending, emp_code ascending)` for attendance-list sorting; and employee indexes on `(department, joined_on)` and `joined_on` for department/headcount and join-date queries. I avoided adding a separate index for every possible filter without evidence that it improves the query plan.

2. **Punch-in race.** Both requests may pass the preliminary existence check, but the unique compound index allows only one insert for that employee and date. The other insert raises `DuplicateKeyError`, which the API converts to HTTP 409.

3. **Ties.** The leaderboard uses MongoDB competition ranking. Employees tied at the cutoff share the same rank, and every row whose rank is at most `limit` is returned, even if that means more than `limit` rows. Rows are then ordered by late minutes descending and employee code ascending.

4. **Headcount.** The department summary starts from employees who joined by month-end and looks up each person's logs. Employees with no logs still contribute to headcount; employees who joined after month-end are excluded.

5. **100x data.** I would load-test with production-like data and inspect execution statistics, then tune indexes and aggregation pipelines based on measured bottlenecks. I would also review pagination and consider a suitable reporting/pre-aggregation strategy if analytics became too expensive.
