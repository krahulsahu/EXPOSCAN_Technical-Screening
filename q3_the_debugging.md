# Question 3 - The Debugging

## 1. The First Three Things to Check (in Order)

1. **Database Cluster Metrics (Resource Saturation)**
   * **Why**: We must determine if the timeout is caused by physical cluster saturation (CPU, RAM, Disk IOPS, or maximum concurrent connection limits) or if the cluster is healthy but suffering from a localized block/long-running query.
   * **Order**: Checked first because cluster-level metrics immediately tell us if the database is running out of capacity.

2. **Active and Slow Database Operations (Profiler/Current Ops)**
   * **Why**: If the database cluster is under high load (or even if it seems idle but connections are timing out), we need to find out which query is hogging resources or holding collection locks. Since no code was deployed, a collection must have grown past a tipping point where an unindexed query has become a performance bottleneck.
   * **Order**: Checked second to pinpoint the exact query filter, collection, and execution path causing the delay.

3. **Application-Side Connection Pool and Network Latency**
   * **Why**: If the database cluster metrics and query logs look completely normal, the bottleneck is on the application side. The FastAPI application might have exhausted its connection pool (e.g., due to unclosed database sessions or client connection leaks) or there is network-level latency/packet loss between the FastAPI servers and the MongoDB cluster.
   * **Order**: Checked third to rule out client-side resource leakage or network routing issues.

---

## 2. What Commands or Tools to Run

### Step 1: Checking DB Cluster Metrics
* **If on MongoDB Atlas**:
  Navigate to the **Metrics** page for the cluster and inspect:
  - **Process CPU**: Look for spikes to 100%.
  - **Disk IOPS**: Check if we have saturated the disk IOPS limit (which throttles read/write speeds).
  - **Connections**: Check if we are approaching the max connection limit of the cluster tier.
* **If self-hosted (ssh into the DB primary instance)**:
  - Run `mongostat --discover` to view real-time statistics (inserts, queries, updates, deletes, active connections, and lock percentages):
    ```bash
    mongostat --host localhost:27017 -n 10 1
    ```
  - Run `mongotop 1` to find out which collections are consuming the most read/write time:
    ```bash
    mongotop --host localhost:27017 1
    ```

### Step 2: Finding Slow/Active Queries
Log into the MongoDB primary via the Mongo Shell (`mongosh`) and run:
* **Find running queries taking longer than 3 seconds**:
  ```javascript
  db.currentOp({
    "active": true,
    "secs_running": { "$gte": 3 },
    "op": { "$in": ["query", "getmore", "command"] }
  })
  ```
  *What to inspect*: Identify the `"command"` field (shows the target collection and query filters) and check if `"waitingForLock"` is `true`.
* **Query the System Profiler for historic slow queries (taking > 200ms)**:
  ```javascript
  // Assumes profiling is enabled (db.setProfilingLevel(1))
  db.system.profile.find({
    "millis": { "$gt": 200 }
  }).sort({ "ts": -1 }).limit(10).pretty()
  ```
  *What to inspect*: Look for query patterns targeting collections that lack indexes, noting the `"query"` document and the execution time in `"millis"`.

### Step 3: Checking Application-Side Resource Issues
* **Filter application logs** in your log aggregator (CloudWatch, Datadog) or directly on the servers using `grep` to trace the exact line of code throwing the exception:
  ```bash
  grep -C 5 -i "timeout" /var/log/fastapi/app.log
  ```
  Look for exceptions like `ServerSelectionTimeoutError` or `NetworkTimeout`.

---

## 3. The Most Likely Cause and How to Confirm It

### Most Likely Cause:
**A missing database index on a collection that has grown past a performance tipping point (triggering a `COLLSCAN`).**

When the application was launched, the collections were small (e.g., under 1,000 documents). A query lacking an index would execute a Full Collection Scan (`COLLSCAN`). Because the database only had to scan 1,000 documents, the query completed in **1ms to 5ms**, which went unnoticed. 

Over three months of steady production use, the collection grew (e.g., to 80,000+ documents). Without an index, MongoDB must scan all 80,000+ documents sequentially. This causes:
1. Query times to rise from **5ms to 5000ms+** (scaling linearly $O(N)$ with document growth).
2. The database CPU to spike to 100% due to continuous disk-to-memory scanning.
3. FastAPI workers to wait for responses, holding their connections open, which quickly exhausts the FastAPI connection pool.
4. The client requests to exceed the driver's default socket timeout (`socketTimeoutMS`, typically 30s) or the API gateway timeout (typically 30s), returning `500 Internal Server Errors`.

### How to Confirm the Cause:

1. **Extract the offending query** from `db.currentOp()` or the application stack trace. For example, let's assume it is:
   ```javascript
   db.leads.find({ "email": "candidate@example.com", "event_id": "event_999" })
   ```
2. **Execute the query with `.explain("executionStats")`** in the MongoDB shell:
   ```javascript
   db.leads.find({ "email": "candidate@example.com", "event_id": "event_999" }).explain("executionStats")
   ```
3. **Analyze the explain output**:
   * **Check the query stage**: Locate `queryPlanner.winningPlan.stage`. If it says **`COLLSCAN`** (Collection Scan) instead of **`IXSCAN`** (Index Scan), there is no index covering this query.
   * **Check documents examined**: Compare `executionStats.totalDocsExamined` against `executionStats.nReturned`. If `totalDocsExamined` is 80,000 and `nReturned` is 1, it means MongoDB scanned the entire database to find a single match.
   * **Check execution time**: Look at `executionStats.executionTimeMillis`. If it is in the thousands of milliseconds, this confirms that the query is timing out the client.
