"""The demo enterprise: a small but realistic 12-service organisation.

This module is the single source of truth for the seeded graph, so the seeder,
the tests and the docs all describe the same company. The topology is chosen so
that the three demo scenarios are genuinely emergent rather than hard-coded:

* ``SVC-AUTH`` sits behind ``SVC-ORDER`` and ``SVC-RISK``, and those feed
  ``SVC-PAY``, ``SVC-BILL`` and ``SVC-NOTIF``. A schema change at the auth
  boundary therefore reaches two Tier-1 services by real traversal.
* ``SVC-BILL`` calls ``SVC-ORDER`` while ``SVC-ORDER`` calls ``SVC-PAY`` which
  calls ``SVC-BILL``. That is a real reconciliation cycle, so cycle detection
  has something honest to find.
* ``ADR-019`` carries a machine-checkable constraint over ``MOD-AUTH-TOKEN``,
  which is exactly the module ``PR-812`` touches.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Track 01 - services, modules, schemas, infrastructure, telemetry
# ---------------------------------------------------------------------------

SERVICES: list[dict[str, Any]] = [
    {"id": "SVC-AUTH", "name": "AuthService", "tier": 1, "sla_ms": 120,
     "criticality_score": 0.94, "status": "healthy", "language": "python",
     "repo": "acme/auth", "tenant_id": "acme"},
    {"id": "SVC-ORDER", "name": "OrderGateway", "tier": 1, "sla_ms": 200,
     "criticality_score": 0.91, "status": "healthy", "language": "python",
     "repo": "acme/orders", "tenant_id": "acme"},
    {"id": "SVC-PAY", "name": "PaymentAPI", "tier": 1, "sla_ms": 250,
     "criticality_score": 0.97, "status": "degraded", "language": "python",
     "repo": "acme/payments", "tenant_id": "acme"},
    {"id": "SVC-BILL", "name": "BillingWorker", "tier": 2, "sla_ms": 500,
     "criticality_score": 0.72, "status": "healthy", "language": "python",
     "repo": "acme/billing", "tenant_id": "acme"},
    {"id": "SVC-CACHE", "name": "SessionCache", "tier": 2, "sla_ms": 20,
     "criticality_score": 0.83, "status": "healthy", "language": "python",
     "repo": "acme/cache", "tenant_id": "acme"},
    {"id": "SVC-USER", "name": "UserProfileService", "tier": 2, "sla_ms": 150,
     "criticality_score": 0.64, "status": "healthy", "language": "python",
     "repo": "acme/users", "tenant_id": "acme"},
    {"id": "SVC-NOTIF", "name": "NotificationService", "tier": 2, "sla_ms": 400,
     "criticality_score": 0.55, "status": "healthy", "language": "python",
     "repo": "acme/notifications", "tenant_id": "acme"},
    {"id": "SVC-RISK", "name": "RiskScoringService", "tier": 2, "sla_ms": 300,
     "criticality_score": 0.68, "status": "healthy", "language": "python",
     "repo": "acme/risk", "tenant_id": "acme"},
    {"id": "SVC-STRIPE", "name": "StripeWebhook", "tier": 3, "sla_ms": 800,
     "criticality_score": 0.41, "status": "healthy", "language": "python",
     "repo": "acme/billing", "tenant_id": "acme"},
    {"id": "SVC-EMAIL", "name": "EmailDispatcher", "tier": 3, "sla_ms": 900,
     "criticality_score": 0.33, "status": "healthy", "language": "python",
     "repo": "acme/notifications", "tenant_id": "acme"},
    {"id": "SVC-ANALYTICS", "name": "AnalyticsIngest", "tier": 3, "sla_ms": 1000,
     "criticality_score": 0.29, "status": "healthy", "language": "python",
     "repo": "acme/analytics", "tenant_id": "acme"},
    {"id": "SVC-SEARCH", "name": "SearchIndexer", "tier": 3, "sla_ms": 700,
     "criticality_score": 0.37, "status": "healthy", "language": "python",
     "repo": "acme/search", "tenant_id": "acme"},
]

#: ``(caller, callee, p99_ms, weight, protocol)`` - caller depends on callee.
CALLS: list[tuple[str, str, float, float, str]] = [
    ("SVC-ORDER", "SVC-AUTH", 42.0, 0.9, "grpc"),
    ("SVC-ORDER", "SVC-PAY", 88.0, 0.8, "grpc"),
    ("SVC-ORDER", "SVC-ANALYTICS", 15.0, 0.3, "http"),
    ("SVC-PAY", "SVC-BILL", 120.0, 0.7, "grpc"),
    # BillingWorker reconciles by reading OrderGateway back: this closes the
    # ORDER -> PAY -> BILL -> ORDER cycle the cycle detector should find.
    ("SVC-BILL", "SVC-ORDER", 95.0, 0.6, "grpc"),
    ("SVC-BILL", "SVC-STRIPE", 210.0, 0.5, "http"),
    ("SVC-STRIPE", "SVC-EMAIL", 60.0, 0.4, "http"),
    ("SVC-AUTH", "SVC-CACHE", 4.0, 1.0, "redis"),
    ("SVC-AUTH", "SVC-USER", 70.0, 0.5, "grpc"),
    ("SVC-USER", "SVC-CACHE", 5.0, 0.8, "redis"),
    ("SVC-USER", "SVC-SEARCH", 130.0, 0.4, "http"),
    ("SVC-NOTIF", "SVC-EMAIL", 55.0, 0.6, "http"),
    ("SVC-NOTIF", "SVC-ANALYTICS", 18.0, 0.3, "http"),
    ("SVC-PAY", "SVC-NOTIF", 65.0, 0.5, "http"),
    ("SVC-PAY", "SVC-RISK", 140.0, 0.7, "grpc"),
    ("SVC-RISK", "SVC-AUTH", 38.0, 0.8, "grpc"),
    ("SVC-SEARCH", "SVC-ANALYTICS", 22.0, 0.3, "http"),
]

MODULES: list[dict[str, Any]] = [
    {"id": "MOD-AUTH-TOKEN", "file_path": "auth/token_service.py", "language": "python",
     "ast_hash": "a1f0c3", "complexity": 24, "loc": 410, "tenant_id": "acme"},
    {"id": "MOD-AUTH-MW", "file_path": "auth/middleware.py", "language": "python",
     "ast_hash": "b2e19a", "complexity": 12, "loc": 180, "tenant_id": "acme"},
    {"id": "MOD-ORDER-GW", "file_path": "order/gateway.py", "language": "python",
     "ast_hash": "c3d47e", "complexity": 31, "loc": 520, "tenant_id": "acme"},
    {"id": "MOD-ORDER-ROUTER", "file_path": "order/router.py", "language": "python",
     "ast_hash": "d4c58f", "complexity": 14, "loc": 240, "tenant_id": "acme"},
    {"id": "MOD-PAY-CORE", "file_path": "payment/payment_service.py", "language": "python",
     "ast_hash": "e5b690", "complexity": 38, "loc": 640, "tenant_id": "acme"},
    {"id": "MOD-PAY-RETRY", "file_path": "payment/retry_policy.py", "language": "python",
     "ast_hash": "f6a7a1", "complexity": 19, "loc": 210, "tenant_id": "acme"},
    {"id": "MOD-BILL-WORKER", "file_path": "billing/worker.py", "language": "python",
     "ast_hash": "0a7b82", "complexity": 27, "loc": 480, "tenant_id": "acme"},
    {"id": "MOD-BILL-RECON", "file_path": "billing/reconciler.py", "language": "python",
     "ast_hash": "1b8c93", "complexity": 22, "loc": 350, "tenant_id": "acme"},
    {"id": "MOD-CACHE-CLIENT", "file_path": "cache/redis_client.py", "language": "python",
     "ast_hash": "2c9da4", "complexity": 9, "loc": 140, "tenant_id": "acme"},
    {"id": "MOD-USER-PROFILE", "file_path": "user/profile_service.py", "language": "python",
     "ast_hash": "3d0eb5", "complexity": 16, "loc": 290, "tenant_id": "acme"},
    {"id": "MOD-NOTIF-DISPATCH", "file_path": "notification/dispatcher.py", "language": "python",
     "ast_hash": "4e1fc6", "complexity": 13, "loc": 230, "tenant_id": "acme"},
    {"id": "MOD-STRIPE-HOOK", "file_path": "billing/stripe_webhook.py", "language": "python",
     "ast_hash": "5f20d7", "complexity": 18, "loc": 310, "tenant_id": "acme"},
    {"id": "MOD-EMAIL-SENDER", "file_path": "notification/email_sender.py", "language": "python",
     "ast_hash": "6031e8", "complexity": 11, "loc": 190, "tenant_id": "acme"},
    {"id": "MOD-ANALYTICS-INGEST", "file_path": "analytics/ingest.py", "language": "python",
     "ast_hash": "7142f9", "complexity": 15, "loc": 260, "tenant_id": "acme"},
    {"id": "MOD-SEARCH-INDEX", "file_path": "search/indexer.py", "language": "python",
     "ast_hash": "82530a", "complexity": 20, "loc": 330, "tenant_id": "acme"},
    {"id": "MOD-RISK-SCORE", "file_path": "payment/risk_scoring.py", "language": "python",
     "ast_hash": "93641b", "complexity": 21, "loc": 300, "tenant_id": "acme"},
]

#: ``(service, module)``
IMPLEMENTED_BY: list[tuple[str, str]] = [
    ("SVC-AUTH", "MOD-AUTH-TOKEN"),
    ("SVC-AUTH", "MOD-AUTH-MW"),
    ("SVC-ORDER", "MOD-ORDER-GW"),
    ("SVC-ORDER", "MOD-ORDER-ROUTER"),
    ("SVC-PAY", "MOD-PAY-CORE"),
    ("SVC-PAY", "MOD-PAY-RETRY"),
    ("SVC-BILL", "MOD-BILL-WORKER"),
    ("SVC-BILL", "MOD-BILL-RECON"),
    ("SVC-CACHE", "MOD-CACHE-CLIENT"),
    ("SVC-USER", "MOD-USER-PROFILE"),
    ("SVC-NOTIF", "MOD-NOTIF-DISPATCH"),
    ("SVC-STRIPE", "MOD-STRIPE-HOOK"),
    ("SVC-EMAIL", "MOD-EMAIL-SENDER"),
    ("SVC-ANALYTICS", "MOD-ANALYTICS-INGEST"),
    ("SVC-SEARCH", "MOD-SEARCH-INDEX"),
    ("SVC-RISK", "MOD-RISK-SCORE"),
]

#: ``(importer, imported, symbol)``
IMPORTS: list[tuple[str, str, str]] = [
    ("MOD-ORDER-GW", "MOD-AUTH-TOKEN", "verify_token"),
    ("MOD-PAY-CORE", "MOD-AUTH-MW", "require_scope"),
    ("MOD-BILL-WORKER", "MOD-ORDER-GW", "fetch_order"),
    ("MOD-STRIPE-HOOK", "MOD-BILL-WORKER", "enqueue_invoice"),
    ("MOD-NOTIF-DISPATCH", "MOD-EMAIL-SENDER", "send_email"),
    ("MOD-RISK-SCORE", "MOD-AUTH-TOKEN", "decode_claims"),
    ("MOD-USER-PROFILE", "MOD-CACHE-CLIENT", "get_session"),
    ("MOD-AUTH-TOKEN", "MOD-CACHE-CLIENT", "cache_session"),
    ("MOD-PAY-RETRY", "MOD-PAY-CORE", "ChargeRequest"),
    ("MOD-SEARCH-INDEX", "MOD-CACHE-CLIENT", "get_session"),
]

SCHEMAS: list[dict[str, Any]] = [
    {"id": "API-AUTH-PAYLOAD", "endpoint": "AuthPayload", "version": "v2",
     "payload_hash": "ph-auth-2", "fields": "sub,roles,scope,exp,iat,sid",
     "tenant_id": "acme"},
    {"id": "API-AUTH-LOGIN", "endpoint": "/v1/auth/login", "version": "v1",
     "payload_hash": "ph-login-1", "fields": "user_id,token,expires_at",
     "tenant_id": "acme"},
    {"id": "API-ORDER-CREATE", "endpoint": "/v1/orders", "version": "v1",
     "payload_hash": "ph-order-1", "fields": "items,currency,idempotency_key",
     "tenant_id": "acme"},
    {"id": "API-PAY-CHARGE", "endpoint": "/v1/payments/charge", "version": "v2",
     "payload_hash": "ph-charge-2", "fields": "amount,currency,idempotency_key",
     "tenant_id": "acme"},
    {"id": "API-BILL-INVOICE", "endpoint": "/v1/billing/invoice", "version": "v1",
     "payload_hash": "ph-invoice-1", "fields": "period,total,line_items",
     "tenant_id": "acme"},
]

#: ``(schema, field_name, type, nullable, breaking, owner_module)``
SCHEMA_FIELDS: list[tuple[str, str, str, bool, bool, str]] = [
    ("API-AUTH-PAYLOAD", "sub", "string", False, False, "MOD-AUTH-TOKEN"),
    ("API-AUTH-PAYLOAD", "roles", "array", False, False, "MOD-AUTH-TOKEN"),
    # ``scope`` is the field PR-812 removes; ADR-019 froze it.
    ("API-AUTH-PAYLOAD", "scope", "string", False, True, "MOD-AUTH-TOKEN"),
    ("API-AUTH-PAYLOAD", "exp", "int", False, False, "MOD-AUTH-TOKEN"),
    ("API-AUTH-PAYLOAD", "iat", "int", False, False, "MOD-AUTH-TOKEN"),
    ("API-AUTH-PAYLOAD", "sid", "string", True, False, "MOD-AUTH-TOKEN"),
    ("API-AUTH-LOGIN", "user_id", "string", False, False, "MOD-AUTH-MW"),
    ("API-AUTH-LOGIN", "token", "string", False, False, "MOD-AUTH-MW"),
    ("API-AUTH-LOGIN", "expires_at", "int", False, False, "MOD-AUTH-MW"),
    ("API-ORDER-CREATE", "items", "array", False, False, "MOD-ORDER-GW"),
    ("API-ORDER-CREATE", "currency", "string", False, False, "MOD-ORDER-GW"),
    ("API-ORDER-CREATE", "idempotency_key", "string", True, False, "MOD-ORDER-GW"),
    ("API-PAY-CHARGE", "amount", "int", False, False, "MOD-PAY-CORE"),
    ("API-PAY-CHARGE", "currency", "string", False, False, "MOD-PAY-CORE"),
    ("API-PAY-CHARGE", "idempotency_key", "string", False, True, "MOD-PAY-CORE"),
    ("API-BILL-INVOICE", "period", "string", False, False, "MOD-BILL-RECON"),
    ("API-BILL-INVOICE", "total", "int", False, False, "MOD-BILL-RECON"),
    ("API-BILL-INVOICE", "line_items", "array", False, False, "MOD-BILL-RECON"),
]

EXPOSES: list[tuple[str, str]] = [
    ("SVC-AUTH", "API-AUTH-PAYLOAD"),
    ("SVC-AUTH", "API-AUTH-LOGIN"),
    ("SVC-ORDER", "API-ORDER-CREATE"),
    ("SVC-PAY", "API-PAY-CHARGE"),
    ("SVC-BILL", "API-BILL-INVOICE"),
]

INFRA: list[dict[str, Any]] = [
    {"id": "INFRA-REDIS-PRIMARY", "kind": "redis", "region": "eu-west-1",
     "config_json": '{"max_connections": 128, "timeout_ms": 250}', "tenant_id": "acme"},
    {"id": "INFRA-PG-ORDERS", "kind": "postgres", "region": "eu-west-1",
     "config_json": '{"max_connections": 200}', "tenant_id": "acme"},
    {"id": "INFRA-K8S-AUTH", "kind": "kubernetes", "region": "eu-west-1",
     "config_json": '{"replicas": 6, "hpa_max": 20}', "tenant_id": "acme"},
]

DEPLOYED_ON: list[tuple[str, str]] = [
    ("SVC-AUTH", "INFRA-K8S-AUTH"),
    ("SVC-CACHE", "INFRA-REDIS-PRIMARY"),
    ("SVC-USER", "INFRA-REDIS-PRIMARY"),
    ("SVC-ORDER", "INFRA-PG-ORDERS"),
]

ALERTS: list[dict[str, Any]] = [
    {"id": "ALT-904", "severity": "critical", "metric": "http_5xx_rate",
     "value": 0.42, "threshold": 0.05, "timestamp": "2026-10-16T16:54:00+00:00",
     "status": "firing",
     "summary": "PaymentAPI 502 spike: 42% of charge requests failing",
     "tenant_id": "acme"},
    {"id": "ALT-880", "severity": "warning", "metric": "redis_connection_utilisation",
     "value": 0.91, "threshold": 0.80, "timestamp": "2026-10-16T16:52:00+00:00",
     "status": "firing",
     "summary": "SessionCache connection pool at 91%",
     "tenant_id": "acme"},
]

TRIGGERED_ON: list[tuple[str, str]] = [
    ("ALT-904", "SVC-PAY"),
    ("ALT-880", "SVC-CACHE"),
]

# ---------------------------------------------------------------------------
# Track 03 - the company brain
# ---------------------------------------------------------------------------

TEAMS: list[dict[str, Any]] = [
    {"id": "TEAM-PLATFORM", "name": "Platform", "slack_channel": "#platform",
     "tenant_id": "acme"},
    {"id": "TEAM-FINOPS", "name": "FinOps", "slack_channel": "#finops",
     "tenant_id": "acme"},
    {"id": "TEAM-GROWTH", "name": "Growth", "slack_channel": "#growth",
     "tenant_id": "acme"},
    {"id": "TEAM-COMMS", "name": "Comms", "slack_channel": "#comms",
     "tenant_id": "acme"},
]

PEOPLE: list[dict[str, Any]] = [
    {"id": "P-PRIYA", "handle": "@priya", "name": "Priya Raghavan",
     "role": "Staff Engineer", "timezone": "Europe/Berlin", "on_call": True,
     "tenant_id": "acme"},
    {"id": "P-VIKAS", "handle": "@vikas", "name": "Vikas Raju",
     "role": "Founder / Engineer", "timezone": "Asia/Kolkata", "on_call": False,
     "tenant_id": "acme"},
    {"id": "P-DANIEL", "handle": "@daniel", "name": "Daniel Osei",
     "role": "Senior Engineer", "timezone": "Europe/London", "on_call": True,
     "tenant_id": "acme"},
    {"id": "P-MEILIN", "handle": "@meilin", "name": "Mei-Lin Chen",
     "role": "Engineer", "timezone": "Asia/Singapore", "on_call": False,
     "tenant_id": "acme"},
    {"id": "P-ARJUN", "handle": "@arjun", "name": "Arjun Patel",
     "role": "Engineer", "timezone": "Asia/Kolkata", "on_call": False,
     "tenant_id": "acme"},
    {"id": "P-SOFIA", "handle": "@sofia", "name": "Sofia Marino",
     "role": "SRE", "timezone": "America/New_York", "on_call": True,
     "tenant_id": "acme"},
]

MEMBER_OF: list[tuple[str, str]] = [
    ("P-PRIYA", "TEAM-PLATFORM"),
    ("P-SOFIA", "TEAM-PLATFORM"),
    ("P-VIKAS", "TEAM-PLATFORM"),
    ("P-DANIEL", "TEAM-FINOPS"),
    ("P-MEILIN", "TEAM-GROWTH"),
    ("P-ARJUN", "TEAM-COMMS"),
]

OWNS: list[tuple[str, str]] = [
    ("TEAM-PLATFORM", "SVC-AUTH"),
    ("TEAM-PLATFORM", "SVC-CACHE"),
    ("TEAM-FINOPS", "SVC-PAY"),
    ("TEAM-FINOPS", "SVC-BILL"),
    ("TEAM-FINOPS", "SVC-RISK"),
    ("TEAM-FINOPS", "SVC-STRIPE"),
    ("TEAM-GROWTH", "SVC-ORDER"),
    ("TEAM-GROWTH", "SVC-USER"),
    ("TEAM-GROWTH", "SVC-SEARCH"),
    ("TEAM-GROWTH", "SVC-ANALYTICS"),
    ("TEAM-COMMS", "SVC-NOTIF"),
    ("TEAM-COMMS", "SVC-EMAIL"),
]

PRS: list[dict[str, Any]] = [
    {"id": "PR-812", "number": 812, "title": "feat(auth): slim down AuthPayload",
     "state": "open", "diff_summary": "Removes the scope claim from AuthPayload and "
     "adds a 180ms token-mint latency budget to AuthService.",
     "created_at": "2026-10-16T16:40:00+00:00", "repo": "acme/auth",
     "tenant_id": "acme"},
    {"id": "PR-402", "number": 402, "title": "perf(payment): bypass redis on retry",
     "state": "merged",
     "diff_summary": "Skips SessionCache lookups on the charge retry path.",
     "created_at": "2026-05-11T09:12:00+00:00", "repo": "acme/payments",
     "tenant_id": "acme"},
    {"id": "PR-651", "number": 651, "title": "refactor(order): gateway retry cleanup",
     "state": "merged",
     "diff_summary": "Consolidates retry helpers in OrderGateway.",
     "created_at": "2026-08-02T14:00:00+00:00", "repo": "acme/orders",
     "tenant_id": "acme"},
    # Opened inside the incident change window: this is the change the triage
    # agent is supposed to surface as the root-cause suspect for ALT-904.
    {"id": "PR-841", "number": 841, "title": "perf(order): raise gateway concurrency",
     "state": "open",
     "diff_summary": "Raises OrderGateway worker concurrency and removes the "
     "inbound request cache, increasing fan-out to PaymentAPI.",
     "created_at": "2026-10-16T16:47:00+00:00", "repo": "acme/orders",
     "tenant_id": "acme"},
]

MODIFIES: list[tuple[str, str, int, float]] = [
    # (pr, module, lines_changed, breaking_risk)
    ("PR-812", "MOD-AUTH-TOKEN", 84, 0.86),
    ("PR-812", "MOD-AUTH-MW", 12, 0.20),
    ("PR-402", "MOD-PAY-RETRY", 46, 0.55),
    ("PR-402", "MOD-CACHE-CLIENT", 8, 0.15),
    ("PR-651", "MOD-ORDER-GW", 61, 0.30),
    ("PR-841", "MOD-ORDER-GW", 37, 0.45),
]

COMMITS: list[dict[str, Any]] = [
    {"id": "CMT-9f31a2", "sha": "9f31a2c", "message": "feat(auth): drop scope claim from AuthPayload",
     "timestamp": "2026-10-16T16:38:00+00:00", "additions": 42, "deletions": 30,
     "tenant_id": "acme"},
    {"id": "CMT-7b20d1", "sha": "7b20d1e", "message": "perf(payment): skip redis on retry",
     "timestamp": "2026-05-11T09:05:00+00:00", "additions": 30, "deletions": 16,
     "tenant_id": "acme"},
    {"id": "CMT-2c88f4", "sha": "2c88f4a", "message": "refactor(order): consolidate retry helpers",
     "timestamp": "2026-08-02T13:58:00+00:00", "additions": 40, "deletions": 21,
     "tenant_id": "acme"},
    {"id": "CMT-51aa09", "sha": "51aa09b", "message": "fix(cache): cap connection pool at 128",
     "timestamp": "2026-04-02T11:20:00+00:00", "additions": 9, "deletions": 2,
     "tenant_id": "acme"},
]

AUTHORED_PERSON_PR: list[tuple[str, str]] = [
    ("P-VIKAS", "PR-812"),
    ("P-DANIEL", "PR-402"),
    ("P-MEILIN", "PR-651"),
    ("P-MEILIN", "PR-841"),
]

REVIEWED: list[tuple[str, str, str, bool]] = [
    ("P-PRIYA", "PR-812", "concerned", False),
    ("P-SOFIA", "PR-402", "approving", True),
    ("P-DANIEL", "PR-651", "approving", True),
    ("P-MEILIN", "PR-812", "neutral", True),
    ("P-SOFIA", "PR-841", "neutral", True),
]

COMMITTED: list[tuple[str, str]] = [
    ("P-VIKAS", "CMT-9f31a2"),
    ("P-DANIEL", "CMT-7b20d1"),
    ("P-MEILIN", "CMT-2c88f4"),
    ("P-PRIYA", "CMT-51aa09"),
]

INCLUDED_IN: list[tuple[str, str]] = [
    ("CMT-9f31a2", "PR-812"),
    ("CMT-7b20d1", "PR-402"),
    ("CMT-2c88f4", "PR-651"),
]

TICKETS: list[dict[str, Any]] = [
    {"id": "TICKET-1420", "key": "AUTH-1420",
     "title": "Reduce auth token payload size for mobile clients",
     "priority": "P2", "status": "in_progress", "tenant_id": "acme"},
    {"id": "TICKET-1101", "key": "PAY-1101",
     "title": "Charge retries saturate Redis connection pool",
     "priority": "P1", "status": "done", "tenant_id": "acme"},
    {"id": "TICKET-1310", "key": "ORD-1310",
     "title": "Consolidate OrderGateway retry helpers",
     "priority": "P3", "status": "done", "tenant_id": "acme"},
]

IMPLEMENTS: list[tuple[str, str]] = [
    ("PR-812", "TICKET-1420"),
    ("PR-402", "TICKET-1101"),
    ("PR-651", "TICKET-1310"),
]

THREADS: list[dict[str, Any]] = [
    {"id": "THREAD-SLACK-991", "platform": "slack", "channel": "#platform",
     "summary": "Priya: 'Dropping scope from the token breaks every downstream "
     "authorisation check. If we need a smaller payload, add a compact claim "
     "instead of removing an existing one.'",
     "source_url": "https://acme.slack.com/archives/C01PLATFORM/p1720500000000000",
     "timestamp": "2026-07-09T10:15:00+00:00", "tenant_id": "acme"},
    {"id": "THREAD-SLACK-880", "platform": "slack", "channel": "#finops",
     "summary": "Daniel: 'Retry storms on the charge path are what took out the "
     "cache. No synchronous retries on this path again - write it down.'",
     "source_url": "https://acme.slack.com/archives/C01FINOPS/p1746950000000000",
     "timestamp": "2026-05-11T09:40:00+00:00", "tenant_id": "acme"},
    {"id": "THREAD-SLACK-1204", "platform": "slack", "channel": "#growth",
     "summary": "Mei-Lin: 'OrderGateway retries duplicated logic; consolidating "
     "them behind one helper so the budget is enforceable.'",
     "source_url": "https://acme.slack.com/archives/C01GROWTH/p1754140000000000",
     "timestamp": "2026-08-02T13:45:00+00:00", "tenant_id": "acme"},
]

DISCUSSED_IN: list[tuple[str, str]] = [
    ("TICKET-1420", "THREAD-SLACK-991"),
    ("TICKET-1101", "THREAD-SLACK-880"),
    ("TICKET-1310", "THREAD-SLACK-1204"),
]

ADRS: list[dict[str, Any]] = [
    {
        "id": "ADR-014", "code": "ADR-014",
        "title": "No synchronous retries on the auth token path",
        "status": "ACCEPTED",
        "rationale": "A retry storm on the token path in May took SessionCache to "
        "91% connection utilisation and produced a 502 cascade. Token minting must "
        "stay under a 200ms p99 budget and must never be retried synchronously.",
        "created_at": "2026-05-12T08:00:00+00:00", "tenant_id": "acme",
        "constraint_json": '{"service": "SVC-AUTH", "rule": "ADR-014 no synchronous retry on token path", "forbids": ["synchronous_retry", "new_call_edge"], "max_latency_p99": 200}',
    },
    {
        "id": "ADR-019", "code": "ADR-019",
        "title": "AuthPayload scope is a frozen contract",
        "status": "ACCEPTED",
        "rationale": "Downstream authorisation depends on the scope claim. It may "
        "be extended but never removed, because removal is a silent breaking change "
        "for consumers that are several hops away from the auth boundary.",
        "created_at": "2026-07-09T11:00:00+00:00", "tenant_id": "acme",
        "constraint_json": '{"service": "SVC-AUTH", "rule": "ADR-019 AuthPayload is a frozen contract", "forbids_breaking_schema": true, "forbids_modules": ["MOD-AUTH-TOKEN"]}',
    },
    {
        "id": "ADR-007", "code": "ADR-007",
        "title": "BillingWorker reconciles through OrderGateway",
        "status": "ACCEPTED",
        "rationale": "Billing must read order state through the gateway so that "
        "order mutations remain auditable in one place.",
        "created_at": "2026-08-03T10:00:00+00:00", "tenant_id": "acme",
        "constraint_json": '{"service": "SVC-BILL", "rule": "ADR-007 billing reads order state via gateway", "forbids": []}',
    },
    {
        "id": "ADR-003", "code": "ADR-003",
        "title": "Redis is a cache, never a source of truth",
        "status": "ACCEPTED",
        "rationale": "Any code path that treats a cache miss as an error state has "
        "caused an outage. Caches may be bypassed but must never be required, and "
        "a retry must never re-enter a cache lookup it has already failed.",
        "created_at": "2026-05-12T10:00:00+00:00", "tenant_id": "acme",
        "constraint_json": '{"service": "SVC-PAY", "rule": "ADR-003 caches are bypassable, never authoritative", "forbids": ["treat_cache_miss_as_error", "synchronous_retry"]}',
    },
]

RESULTED_IN_DECISION: list[tuple[str, str]] = [
    # The FinOps retry debate is what produced the cache-bypass rule that the
    # payment retry path cites - not the auth ADR. Wiring it to ADR-014 would
    # make the provenance timeline point at an unrelated decision.
    ("THREAD-SLACK-880", "ADR-003"),
    ("THREAD-SLACK-991", "ADR-019"),
    ("THREAD-SLACK-1204", "ADR-007"),
]

AUTHORED_PERSON_ADR: list[tuple[str, str]] = [
    ("P-PRIYA", "ADR-019"),
    ("P-DANIEL", "ADR-014"),
    ("P-DANIEL", "ADR-003"),
    ("P-MEILIN", "ADR-007"),
]

GOVERNS: list[tuple[str, str]] = [
    ("ADR-014", "SVC-AUTH"),
    ("ADR-019", "SVC-AUTH"),
    ("ADR-007", "SVC-BILL"),
    ("ADR-003", "SVC-PAY"),
    ("ADR-003", "SVC-CACHE"),
]

CONSTRAINS: list[tuple[str, str]] = [
    ("ADR-019", "API-AUTH-PAYLOAD"),
    ("ADR-014", "API-AUTH-LOGIN"),
    ("ADR-007", "API-BILL-INVOICE"),
    ("ADR-003", "API-PAY-CHARGE"),
]

# ---------------------------------------------------------------------------
# Track 02 - seeded episodic and procedural memory
# ---------------------------------------------------------------------------

EPISODES: list[dict[str, Any]] = [
    {
        "id": "INC-2201",
        "summary": "PaymentAPI 502 spike caused by a Redis connection pool leak on "
        "the charge retry path.",
        "severity": "SEV1",
        "mttr_seconds": 1680,
        "occurred_at": "2026-09-25T02:10:00+00:00",
        "affected": ["SVC-PAY", "SVC-CACHE"],
        # Symptom service drives the structural signature. The seeder computes
        # the signature by traversing from this service using the same helper
        # live triage uses, so the two are always comparable.
        "symptom": "SVC-PAY",
    },
    {
        "id": "INC-1802",
        "summary": "Auth latency regression after token payload growth pushed "
        "AuthService past its p99 budget.",
        "severity": "SEV2",
        "mttr_seconds": 900,
        "occurred_at": "2026-07-10T14:05:00+00:00",
        "affected": ["SVC-AUTH"],
        "symptom": "SVC-AUTH",
    },
    {
        "id": "INC-1610",
        "summary": "OrderGateway retry storm amplified a downstream billing backlog.",
        "severity": "SEV2",
        "mttr_seconds": 2400,
        "occurred_at": "2026-05-11T09:55:00+00:00",
        "affected": ["SVC-ORDER", "SVC-BILL"],
        "symptom": "SVC-ORDER",
    },
]

PLAYBOOKS: list[dict[str, Any]] = [
    {
        "id": "PB-09",
        "name": "Drain and revert the payment retry path",
        "steps": [
            {"step": 1, "action": "quarantine_endpoint",
             "target": "SVC-PAY", "detail": "Isolate the charge endpoint behind a circuit breaker."},
            {"step": 2, "action": "create_revert_pr",
             "target": "MOD-PAY-RETRY", "detail": "Revert the retry-policy change that introduced the leak."},
            {"step": 3, "action": "page_domain_expert",
             "target": "TEAM-FINOPS", "detail": "Page the FinOps on-call owner of the charge path."},
        ],
        "success_rate": 0.93,
        "uses": 4,
        # The trigger signature is inherited from the incident that produced the
        # playbook, which is what makes recall structural rather than lexical.
        "derived_from": "INC-2201",
    },
    {
        "id": "PB-04",
        "name": "Roll back an oversized auth payload",
        "steps": [
            {"step": 1, "action": "create_revert_pr",
             "target": "MOD-AUTH-TOKEN", "detail": "Revert the token payload change."},
            {"step": 2, "action": "sync_stale_adr_doc",
             "target": "ADR-019", "detail": "Re-sync the frozen-contract ADR with the code."},
        ],
        "success_rate": 0.88,
        "uses": 2,
        "derived_from": "INC-1802",
    },
]

SEMANTIC_RULES: list[dict[str, Any]] = [
    {"id": "SR-01",
     "invariant_statement": "No request path may traverse more than three "
     "synchronous service hops.",
     "domain": "architecture", "enforcement_level": "MUST", "tenant_id": "acme",
     "source": "ADR-007 review"},
    {"id": "SR-02",
     "invariant_statement": "Every Tier-1 service must have a declared p99 "
     "latency budget enforced by an ADR.",
     "domain": "reliability", "enforcement_level": "MUST", "tenant_id": "acme",
     "source": "post-incident INC-1802"},
    {"id": "SR-03",
     "invariant_statement": "A cache may be bypassed but never required; a cache "
     "miss is not an error state.",
     "domain": "architecture", "enforcement_level": "SHOULD", "tenant_id": "acme",
     "source": "ADR-003"},
    {"id": "SR-04",
     "invariant_statement": "The AuthPayload scope claim is a frozen contract and "
     "must not be removed or made optional.",
     "domain": "auth", "enforcement_level": "MUST", "tenant_id": "acme",
     "source": "ADR-019"},
    {"id": "SR-05",
     "invariant_statement": "No synchronous retries may be added to the auth "
     "token path.",
     "domain": "auth", "enforcement_level": "MUST", "tenant_id": "acme",
     "source": "ADR-014"},
    {"id": "SR-06",
     "invariant_statement": "Billing reads order state only through OrderGateway, "
     "never directly from the order store.",
     "domain": "payments", "enforcement_level": "MUST", "tenant_id": "acme",
     "source": "ADR-007"},
    {"id": "SR-07",
     "invariant_statement": "Cache misses on the charge path must degrade to the "
     "database, never to an error.",
     "domain": "payments", "enforcement_level": "MUST", "tenant_id": "acme",
     "source": "ADR-003"},
]


#: Which semantic-memory domain each service's invariants live in. Triage looks
#: up the symptom service's domain so a payment incident recalls the payment
#: invariants and not the whole rule book.
SERVICE_DOMAINS: dict[str, str] = {
    "SVC-AUTH": "auth",
    "SVC-PAY": "payments",
    "SVC-BILL": "payments",
    "SVC-STRIPE": "payments",
    "SVC-CACHE": "architecture",
    "SVC-ORDER": "architecture",
    "SVC-USER": "architecture",
    "SVC-ANALYTICS": "architecture",
    "SVC-RISK": "reliability",
    "SVC-NOTIF": "reliability",
    "SVC-SEARCH": "reliability",
    "SVC-EMAIL": "reliability",
}


# ---------------------------------------------------------------------------
# Diff fixtures used by the demo scenarios
# ---------------------------------------------------------------------------

PR_812_DIFF = """diff --git a/auth/token_service.py b/auth/token_service.py
--- a/auth/token_service.py
+++ b/auth/token_service.py
@@ -18,9 +18,9 @@ class TokenService:
-    def mint(self, subject: str, scope: str, ttl: int) -> dict:
-        payload = {"sub": subject, "scope": scope, "exp": ttl}
+    def mint(self, subject: str, ttl: int) -> dict:
+        payload = {"sub": subject, "exp": ttl}
         return self._sign(payload)
 
-    def verify(self, token: str) -> dict:
-        return self._decode(token)
+    def verify(self, token: str) -> dict:
+        return self._decode(token)
+
+    def refresh(self, token: str) -> dict:
+        return self._reissue(token)
diff --git a/auth/middleware.py b/auth/middleware.py
--- a/auth/middleware.py
+++ b/auth/middleware.py
@@ -4,6 +4,7 @@
 import time
+from cache.redis_client import cache_session
 
 def require_scope(claims: dict, scope: str) -> bool:
-    return scope in claims.get("scope", "").split()
+    return bool(claims.get("roles"))
"""

PR_402_DIFF = """diff --git a/payment/retry_policy.py b/payment/retry_policy.py
--- a/payment/retry_policy.py
+++ b/payment/retry_policy.py
@@ -12,7 +12,7 @@ class RetryPolicy:
-    def retry(self, request: ChargeRequest, attempts: int = 3) -> ChargeResult:
+    def retry(self, request: ChargeRequest, attempts: int = 3, cache=None) -> ChargeResult:
         for attempt in range(attempts):
             try:
-                return self._charge(request)
+                return self._charge(request, skip_cache=True)
             except TransientError:
                 continue
"""


def summary() -> dict:
    """Counts of everything the seeder writes, used by the CLI and tests."""
    return {
        "services": len(SERVICES),
        "calls": len(CALLS),
        "modules": len(MODULES),
        "implemented_by": len(IMPLEMENTED_BY),
        "imports": len(IMPORTS),
        "schemas": len(SCHEMAS),
        "schema_fields": len(SCHEMA_FIELDS),
        "exposes": len(EXPOSES),
        "infra": len(INFRA),
        "alerts": len(ALERTS),
        "teams": len(TEAMS),
        "people": len(PEOPLE),
        "prs": len(PRS),
        "commits": len(COMMITS),
        "tickets": len(TICKETS),
        "threads": len(THREADS),
        "adrs": len(ADRS),
        "episodes": len(EPISODES),
        "playbooks": len(PLAYBOOKS),
        "semantic_rules": len(SEMANTIC_RULES),
    }
