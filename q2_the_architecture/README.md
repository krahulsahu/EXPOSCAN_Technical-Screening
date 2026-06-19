# Question 2 - The Architecture

## 1. Pick one data isolation approach and justify it
For a trade show lead capture PWA expecting **50 to 200 tenants at launch**, I select and recommend the **database-per-tenant** approach over a shared collection.

### Context-Specific Justification:

1. **MongoDB Connection Pool Efficiency**:
   In traditional relational databases (like PostgreSQL or MySQL), database-per-tenant is notoriously hard to scale because each database requires its own connection pool. This leads to connection exhaustion very quickly. 
   In contrast, **MongoDB (using Motor/PyMongo)** handles database names dynamically on a single client connection. When you reference `client["tenant_db"]`, it uses the existing client connection pool and targets the specific database. There is **zero connection overhead** or pool duplication for separate databases on the same MongoDB instance.

2. **Compliance, GDPR, and Data Erasure**:
   Exhibitors at trade shows are highly sensitive about their leads. Often, an exhibitor will request a complete export of their data at the end of the show and demand that all their data be permanently purged from the system. 
   * Under a **shared collection model**, deleting a tenant's data requires running `$deleteMany` queries across multiple collections. If there are code bugs or schema updates, orphans can easily be left behind.
   * Under **database-per-tenant**, purging a tenant is a clean, atomic, $O(1)$ database drop command: `await client.drop_database("exposcan_tenant_123")`. It guarantees 100% data isolation and compliance with no risk of residual data.

3. **Operational Backup & Restore Independence**:
   If a client misconfigures their integrations or runs a faulty bulk import that corrupts their leads, database-per-tenant allows us to restore *only* that tenant's database from a snapshot without affecting the other 199 active tenants. Doing this in a shared collection requires spinning up a temporary cluster, extracting specific records, running deletion queries, and performing a partial import—an operationally risky and time-consuming process.

4. **Scale at Launch**:
   At 50 to 200 tenants, MongoDB easily handles the metadata overhead. Since we expect bursty traffic during expos and complete silence otherwise, having isolated databases lets us easily scale high-volume tenants to dedicated instances in the future if they grow too large.

---

## 2. FastAPI Tenant Isolation Middleware / Dependency
The implementation details can be found in [middleware.py](./middleware.py).

### How it works:
* **Sanitization**: We apply a strict regular expression `^[a-zA-Z0-9_-]{3,50}$` to the `X-Tenant-ID` header. This is critical because the tenant ID is interpolated directly into the database name. Without this, a malicious user could perform database name injection (e.g., trying to access internal MongoDB database names like `admin` or `config`).
* **Dynamic Connection Sharing**: The database is resolved on the fly using `_mongo_client[f"exposcan_tenant_{tenant_id}"]`. This leverages PyMongo's multiplexing over the unified connection pool.
* **Context Propagation**: We support both a **Dependency Injection** pattern (`Depends(get_tenant_db)`) and an **ASGI Middleware** pattern utilizing Python's `contextvars`. Using `ContextVar` ensures that the tenant database context is bound to the specific asynchronous event loop task and cannot leak to concurrent requests.

---

## 3. What is the single biggest risk in this migration and how do you mitigate it?

### The Single Biggest Risk:
**"Leaky Contexts" in background tasks or connection routing.**
In a single-tenant app, developers write queries pointing to a global `db` variable. In a multi-tenant migration, the greatest risk is that a developer forgets to pass the dynamic tenant-scoped database object, or a background worker (like Celery or a FastAPI `BackgroundTasks` thread) executes a query using a default, stale, or global database context. This would result in writing one tenant's leads to another tenant's database, or exposing tenant A's dashboard to tenant B.

### Mitigation Strategy:

1. **Strict Dependency Injection & Elimination of Global State**:
   Remove any global references to `db` in the repository or service layers. The database client must be passed explicitly to repository constructors (e.g., `LeadRepository(db: AsyncIOMotorDatabase)`). This forces the compiler/interpreter to fail if a developer attempts to call a repository without a valid, request-scoped tenant database instance.

2. **Automated Cross-Tenant Test Coverage**:
   Create an integration test suite that explicitly spins up two distinct dummy tenants (e.g., `test_tenant_a` and `test_tenant_b`).
   * Perform operations for `test_tenant_a` and assert that the database for `test_tenant_b` remains completely empty.
   * Attempt to access `test_tenant_a`'s data using `test_tenant_b`'s credentials and assert a `404 Not Found` or `401 Unauthorized` is returned.

3. **Linter / Static Analysis Rules**:
   Implement a custom AST (Abstract Syntax Tree) check or linting script in the CI pipeline that fails the build if any database import pattern resembling `from app.db import db` is found in the repository files, ensuring developers are forced to use the injected context.
