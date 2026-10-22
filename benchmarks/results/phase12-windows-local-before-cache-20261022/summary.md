# Phase 12 Benchmark Summary

| Workload | Concurrency | Requests | RPS | p50 ms | p95 ms | p99 ms | Error rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| direct_adapter | 1 | 1000 | 134544.231 | 0.004 | 0.005 | 0.008 | 0.0 |
| direct_adapter | 10 | 1000 | 132380.196 | 0.004 | 0.005 | 0.008 | 0.0 |
| direct_adapter | 50 | 1000 | 133761.37 | 0.004 | 0.005 | 0.006 | 0.0 |
| direct_adapter | 100 | 1000 | 137211.855 | 0.004 | 0.006 | 0.006 | 0.0 |
| executor_audit_telemetry | 1 | 1000 | 564.145 | 1.644 | 2.545 | 3.187 | 0.0 |
| executor_audit_telemetry | 10 | 1000 | 569.146 | 1.657 | 2.553 | 3.092 | 0.0 |
| executor_audit_telemetry | 50 | 1000 | 539.38 | 1.67 | 2.914 | 3.629 | 0.0 |
| executor_audit_telemetry | 100 | 1000 | 553.693 | 1.681 | 2.933 | 3.763 | 0.0 |
| executor_core | 1 | 1000 | 624.301 | 1.49 | 2.443 | 3.111 | 0.0 |
| executor_core | 10 | 1000 | 636.408 | 1.48 | 2.211 | 2.765 | 0.0 |
| executor_core | 50 | 1000 | 634.452 | 1.474 | 2.286 | 2.917 | 0.0 |
| executor_core | 100 | 1000 | 625.335 | 1.532 | 2.324 | 2.949 | 0.0 |
| executor_failure | 1 | 1000 | 637.587 | 1.502 | 2.093 | 2.739 | 1.0 |
| executor_failure | 10 | 1000 | 639.537 | 1.478 | 2.129 | 3.006 | 1.0 |
| executor_failure | 50 | 1000 | 649.613 | 1.479 | 1.966 | 2.845 | 1.0 |
| executor_failure | 100 | 1000 | 629.843 | 1.528 | 2.196 | 3.121 | 1.0 |
| mcp_in_memory | 1 | 1000 | 484.662 | 211.682 | 400.229 | 434.986 | 0.0 |
| mcp_in_memory | 10 | 1000 | 504.918 | 180.445 | 361.885 | 380.073 | 0.0 |
| mcp_in_memory | 50 | 1000 | 505.81 | 104.587 | 302.737 | 334.994 | 0.0 |
| mcp_in_memory | 100 | 1000 | 512.488 | 190.456 | 244.562 | 246.426 | 0.0 |
| postgres_mutation_preview_confirm | 1 | 1000 | 160.584 | 6.108 | 7.673 | 8.607 | 0.0 |
| postgres_mutation_preview_confirm | 10 | 1000 | 164.058 | 5.983 | 7.564 | 8.688 | 0.0 |
| rest_jwt_authenticated | 1 | 1000 | 385.197 | 2.46 | 3.439 | 3.95 | 0.0 |
| rest_jwt_authenticated | 10 | 1000 | 413.478 | 2.3 | 3.407 | 4.067 | 0.0 |
| rest_jwt_authenticated | 50 | 1000 | 409.3 | 2.286 | 3.413 | 4.123 | 0.0 |
| rest_jwt_authenticated | 100 | 1000 | 410.549 | 2.322 | 3.213 | 3.848 | 0.0 |
