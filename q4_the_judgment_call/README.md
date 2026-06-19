# Question 4 - The Judgment Call

## 1. Prioritization & Rationale: Which do you fix first and why?

I prioritize the fixes in the following order:

1. **Priority 1: No indexes on the `leads` collection** (Severity: **Critical / Availability Block**)
2. **Priority 2: Non-atomic `Idempotency-Key` check** (Severity: **High / Data Integrity**)
3. **Priority 3: Client-side setting of `created_at`** (Severity: **Medium / Data Quality**)

### Justification:

* **Why Indexes First?**
  A missing index on a collection of 80,000 documents growing daily is a **ticking time bomb**. As the collection scales to 100k+, queries will transition from "sluggish" to "timed out". Under peak traffic hours (typical for trade shows), multiple concurrent table scans will consume 100% database CPU, locking up all other operations and causing a **total app crash**. Keeping the service online is our absolute baseline responsibility. Fixing this takes minutes, has zero risk, and completely eliminates the availability threat.
  
* **Why Idempotency Second?**
  A non-atomic idempotency check creates race conditions. If a user on a shaky 3G connection at a trade show clicks "Submit" twice, the app will save duplicate leads. This is a severe logic defect that ruins leads analytics and irritates clients (they pay for unique leads and export clean sheets). It is a critical data-integrity bug, but it does not crash the server for other users, making it secondary to the indexing issue.

* **Why Client-Side Timestamps Last?**
  Relying on client-side clocks causes skewed history (because user devices have different system times, incorrect time zones, or manual settings). This is bad for chronological sorting. However, it is a data-consistency issue that does not cause system outages or duplicate database entries. It is a one-line code fix that can be packaged into the next routine deployment.

---

## 2. Technical Fixes

### A. The Most Critical Fix: Creating Indexes
Creating indexes in MongoDB must be handled carefully to avoid blocking active writes in production. In modern MongoDB (>= 4.2), indexes are built dynamically without locking the database.

Here is the programmatic startup script/migration fix to create the required compound indexes:

```python
# db_migration.py
import logging
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

async def ensure_database_indexes(db: AsyncIOMotorDatabase):
    """
    Ensures that the necessary indexes exist on the leads collection.
    """
    try:
        # 1. Compound index for tenant-isolated queries sorted by time (the hot path)
        await db.leads.create_index(
            [("tenant_id", 1), ("event_id", 1), ("created_at", -1)],
            name="idx_tenant_event_created"
        )
        
        # 2. Index for scanning unique email/phone inquiries within a tenant
        await db.leads.create_index(
            [("tenant_id", 1), ("email", 1)],
            name="idx_tenant_lead_email"
        )
        
        logger.info("Successfully verified and created database indexes.")
    except Exception as e:
        logger.error(f"Failed to create indexes: {str(e)}")
```

### B. The Atomicity Fix: Atomic Idempotency-Key
The complete code implementation is available in [idempotency.py](./idempotency.py).

---

## 3. Communication Strategy for a Non-Technical Founder

To explain these findings to a non-technical founder, I will use **real-world analogies**, focus on **business risks**, and present a **reassuring action plan**. I want to show that I have everything under control, without using intimidating technical jargon.

### Communication Script:

> "Hi Sahil,
> 
> Now that I've spent my first week reviewing the ExpoScan codebase, I have a clear picture of the system. The product is working well, and we are in a great position to scale.
> 
> To ensure we stay stable as we grow from 80,000 leads to millions, I've identified three technical adjustments that I want to implement. I've categorized them by their business impact:
> 
> 1. **System Stability (High Priority)**:
>    * **The Issue**: Right now, our database is like a large library without a catalog card system. When our app searches for a lead, it has to scan every single page in the library from start to finish. At 80,000 pages, it works, but as we grow, this will cause the app to slow down and eventually freeze during a busy show.
>    * **The Fix**: I will build a 'digital catalog' (called database indexes). This is a quick, zero-downtime fix that will speed up search times from seconds to milliseconds and ensure our app stays online during major events.
> 
> 2. **Data Cleanliness (Medium Priority)**:
>    * **The Issue**: Currently, if a user double-taps the 'save lead' button on a slow network, the app can accidentally save the same lead twice. This leads to duplicate records, which makes our clients' lead exports look messy.
>    * **The Fix**: I will add an atomic 'double-click lock' on our server. If the server receives two identical submissions in the same split-second, it will block the second one and keep our data clean.
> 
> 3. **Time Accuracy (Low Priority)**:
>    * **The Issue**: Right now, the time a lead is saved is determined by the salesperson's phone clock. If a salesperson's phone is set to the wrong timezone or is 10 minutes fast, our reports will show incorrect timestamps.
>    * **The Fix**: I will shift this task so that our central server stamps the exact time a lead is received. This ensures all exports have a perfectly unified timeline.
> 
> **Next Steps**:
> I have already written the code for these fixes. I will apply the database indexing fix tonight during low-traffic hours (it takes about 2 minutes and has zero risk of downtime), and I will release the double-click lock and timestamp fixes in our scheduled update tomorrow. 
> 
> Let me know if you have any questions!"
