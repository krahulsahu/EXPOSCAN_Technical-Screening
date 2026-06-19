# Question 1 - The Build

### 1. What was the problem it solved?
In trade show and exhibition environments (like large-scale design and decor expos), internet connectivity is notoriously unstable, overloaded, or non-existent. Our users (exhibitors) needed to scan visitor badges, capture leads, and fill out custom qualifications forms without any lag. 
If the application relied on real-time online write operations, the user experience would fail instantly under poor network conditions. However, simple offline storage was insufficient because multiple sales representatives from the same exhibitor needed to see synced leads in real time when connectivity was momentarily restored, and managers wanted to view live analytics dashboards. We needed an **offline-first, high-concurrency lead synchronization and processing engine** that could handle bursty sync traffic (tens of thousands of concurrent writes during trade show peak hours) without data loss, duplicate records, or database lockups.

---

### 2. What did you build and what were the key technical decisions?
I designed and built **EventSync**, a synchronization backend using **FastAPI (Python)**, **Celery**, and **MongoDB**, integrated with a PWA (using SQLite/RxDB on the client).

**Key Technical Decisions & Justifications:**
* **Ingestion Queue Pattern**: Rather than writing incoming synced leads directly to the primary database, the FastAPI endpoint validated the payloads (via Pydantic) and immediately pushed them into a **RabbitMQ** broker. Celery workers then consumed and processed the sync queues asynchronously. This ensured the API response time remained under **15ms**, preventing client timeout loops on shaky cellular networks.
* **MongoDB for Storage**: Lead data at trade shows is unstructured and dynamic. One exhibitor captures basic email/phone; another captures custom questionnaire data, business card OCR results, and product interest tags. MongoDB’s document model allowed us to store highly dynamic schemas without running complex migrations for every tenant’s custom fields.
* **Vector Clocks for Conflict Resolution**: For offline modifications (e.g., two reps editing the same lead notes offline), we implemented client-side auto-incrementing version numbers and vector clocks. The sync engine resolved conflicts on the server by applying changes deterministically using a "last-write-wins" policy on a field-by-field basis, rather than overwriting the entire document.

---

### 3. Was it multi-tenant? If yes, how did you handle data isolation?
Yes, it was a multi-tenant SaaS product. We implemented a **database-per-tenant** isolation model.

**Data Isolation Implementation:**
* Every tenant (client organization) was provisioned a separate MongoDB database (e.g., `tenant_company_a`, `tenant_company_b`) within a shared MongoDB Atlas cluster.
* In the FastAPI application, we used **Motor** (the asynchronous MongoDB driver). Since Motor dynamically binds database names at runtime (i.e., `client[tenant_db_name]`) while sharing the underlying connection pool, we did not have the connection overhead typically associated with database-per-tenant setups in relational databases.
* The authenticated tenant context was extracted from the JWT token on every request, and a custom FastAPI dependency resolved the corresponding tenant database dynamically. This provided absolute data isolation at the database level, simplified GDPR compliance (dropping a database permanently deleted all tenant data immediately), and made tenant-specific database backups/restores straightforward.

---

### 4. What broke in production and how did you fix it?
**The Break:**
During a major industrial fair with over 180 active tenants and 1,200 concurrent sales reps, the application began throwing `500 Internal Server Errors` with `ServerSelectionTimeoutError` (MongoDB connection timeouts) and sync requests began queuing up indefinitely. 

**The Root Cause Analysis:**
1. **Connection Pool Exhaustion**: We ran multiple Celery worker processes with high concurrency settings. Each process was spinning up its own connection pool to MongoDB. During peak hours, the number of active database connections crossed the MongoDB Atlas cluster limit, causing the database to reject new connections.
2. **Unindexed Aggregations**: A manager dashboard endpoint was performing runtime `$lookup` (join) aggregates between the `leads` collection and a global `exhibitors` configuration collection to compute real-time lead distribution. As the `leads` collection reached over 400,000 records, these unindexed joins forced full collection scans (COLLSCAN) which saturated the MongoDB CPU to 100%, cascading the timeouts.

**The Fix:**
* **Immediate Mitigation**: 
  - Reduced the Celery worker concurrency pool sizes and configured the Motor client with a strict `maxPoolSize=20` (down from the default 100) and `minPoolSize=5` to cap the total connections.
  - Implemented backpressure on the FastAPI ingestion routes, returning a `429 Too Many Requests` to client devices when RabbitMQ queues crossed a threshold, forcing the client PWAs to back off and retry sync in increments.
* **Long-term Architectural Fix**:
  - Eliminated runtime `$lookup` aggregates on the hot path. We denormalized static exhibitor data directly into the lead documents at write-time.
  - Set up background cron jobs using Celery Beat to pre-aggregate dashboard statistics into a dedicated `hourly_analytics` collection, reducing dashboard query time from **~8 seconds to under 20 milliseconds**.
  - Added compound indexes on `(tenant_id, event_id, created_at)` to prevent any future COLLSCANs.
