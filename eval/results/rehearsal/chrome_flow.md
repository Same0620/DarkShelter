browser: Google Chrome 155.0.8059.40 (channel=chrome, its own temporary profile)

## http://127.0.0.1:8000 and http://127.0.0.1:8100 — 「阿公在C區3床，他的氧氣快用完了」
  draft B-003 (P1) after 0.5 s
  confirmed B-003: on the desk board
  EOC page shows B-003 after 0.9 s
  desk board shows B-003 已派遣 after 6.9 s
  POST 200 http://127.0.0.1:8000/api/intake/text  Origin=http://127.0.0.1:8000  Sec-Fetch-Site=same-origin  Content-Type=application/json
  POST 200 http://127.0.0.1:8000/api/tickets/3/confirm  Origin=http://127.0.0.1:8000  Sec-Fetch-Site=same-origin  Content-Type=application/json
  POST 200 http://127.0.0.1:8000/api/link/profile  Origin=http://127.0.0.1:8000  Sec-Fetch-Site=same-origin  Content-Type=application/json
  POST 200 http://127.0.0.1:8100/api/tickets/7/3/status  Origin=http://127.0.0.1:8100  Sec-Fetch-Site=same-origin  Content-Type=application/json
  refused by the guard: none

## http://localhost:8000 and http://localhost:8100 — 「我媽在A區7床，明天下午要洗腎」
  draft B-004 (P1) after 0.5 s
  confirmed B-004: on the desk board
  EOC page shows B-004 after 0.9 s
  desk board shows B-004 已派遣 after 1.9 s
  POST 200 http://localhost:8000/api/intake/text  Origin=http://localhost:8000  Sec-Fetch-Site=same-origin  Content-Type=application/json
  POST 200 http://localhost:8000/api/tickets/4/confirm  Origin=http://localhost:8000  Sec-Fetch-Site=same-origin  Content-Type=application/json
  POST 200 http://localhost:8000/api/link/profile  Origin=http://localhost:8000  Sec-Fetch-Site=same-origin  Content-Type=application/json
  POST 200 http://localhost:8100/api/tickets/7/4/status  Origin=http://localhost:8100  Sec-Fetch-Site=same-origin  Content-Type=application/json
  refused by the guard: none

RESULT: both addresses work end to end
