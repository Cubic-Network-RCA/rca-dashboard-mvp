from datetime import date, timedelta
import sqlite3
import random
import string

def gen_id(prefix):
    return f"{prefix}-" + "".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(7))

def seed_demo(db_path="rca.db"):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    today = date.today()

    # A Nissan-like recurrence example
    rca1 = gen_id("RCA")
    cur.execute("""
        INSERT OR IGNORE INTO rcas (rca_id,oem,environment,system_component,severity,title,root_cause,created_by,created_at,status)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (rca1, "Nissan", "UAT", "Auth/API Gateway", "P2",
          "Intermittent session timeout during high-latency calls",
          "Gateway timeout thresholds not aligned across environments; missing regression test for slow responses.",
          "TAM/PMO", (today - timedelta(days=70)).isoformat(), "Open"))

    act1 = gen_id("ACT")
    cur.execute("""
        INSERT OR IGNORE INTO actions (action_id,rca_id,action_text,action_type,owner_team,owner_person,due_date,status,verification_method)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (act1, rca1,
          "Align timeout config across UAT and Production; add regression test simulating 95th percentile latency; attach before/after config + test run evidence.",
          "Config", "Tech", "Owner A", (today - timedelta(days=10)).isoformat(), "In Progress",
          "Config diff + regression test run + monitoring screenshot"))

    # Another action with evidence + verified
    act2 = gen_id("ACT")
    cur.execute("""
        INSERT OR IGNORE INTO actions (action_id,rca_id,action_text,action_type,owner_team,owner_person,due_date,status,verification_method,verified_by,verified_at,verification_notes)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (act2, rca1,
          "Add alerting for elevated gateway timeouts; validate alert triggers in UAT and capture screenshot.",
          "Detect", "Tech", "Owner B", (today - timedelta(days=20)).isoformat(), "Verified",
          "Alert config + test trigger evidence", "QA Lead", (today - timedelta(days=15)).isoformat(),
          "Alert triggered as expected during simulated timeout."))

    ev1 = gen_id("EVD")
    cur.execute("""
        INSERT OR IGNORE INTO evidence (evidence_id,action_id,evidence_type,evidence_ref,submitted_by,submitted_at)
        VALUES (?,?,?,?,?,?)
    """, (ev1, act2, "Monitoring note", "Screenshot: Alert fired for simulated timeout (UAT)", "Owner B", (today - timedelta(days=16)).isoformat()))

    # Pre-Live RCAs within 6 months (low volume)
    for i in range(4):
        rca = gen_id("RCA")
        created_at = (today - timedelta(days=random.randint(5, 175))).isoformat()
        cur.execute("""
            INSERT OR IGNORE INTO rcas (rca_id,oem,environment,system_component,severity,title,root_cause,created_by,created_at,status)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (rca, random.choice(["Nissan","OEM-X","OEM-Y"]), "Pre-Live",
              random.choice(["Payments","Telemetry","Provisioning","Reporting"]), random.choice(["P2","P3","P4"]),
              random.choice(["UAT data mismatch carried into pre-live","Retry logic missing for transient 502s","Config drift between environments","Missing test coverage for edge case"]),
              "Seeded demo RCA for audit view. Actions need evidence + verification.",
              "PMO", created_at, "Open"))

        # Actions
        for j in range(random.randint(1,3)):
            act = gen_id("ACT")
            due = (today + timedelta(days=random.randint(-20, 25))).isoformat()
            status = random.choice(["To Do","In Progress","Evidence Submitted"])
            cur.execute("""
                INSERT OR IGNORE INTO actions (action_id,rca_id,action_text,action_type,owner_team,owner_person,due_date,status,verification_method)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (act, rca,
                  random.choice(["Add regression test + attach test run output",
                                 "Update config and attach change record link",
                                 "Implement code fix and attach PR + release note",
                                 "Add monitoring dashboard panel and screenshot evidence"]),
                  random.choice(["Test coverage","Config","Code fix","Detect"]),
                  "Tech", random.choice(["Owner C","Owner D","Owner E"]), due, status,
                  "Evidence link + independent verification"))

    # Recent incident that 'repeats' Nissan issue
    inc = gen_id("INC")
    cur.execute("""
        INSERT OR IGNORE INTO incidents (incident_id,oem,environment,system_component,severity,summary,created_at,linked_rca_id)
        VALUES (?,?,?,?,?,?,?,?)
    """, (inc, "Nissan", "Production", "Auth/API Gateway", "P2",
          "Timeout observed again in Production for high latency calls; resembles prior UAT timeout issue.",
          (today - timedelta(days=3)).isoformat(), None))

    conn.commit()
    conn.close()
