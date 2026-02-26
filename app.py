import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, date, timedelta
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

DB_PATH = "rca.db"

# ---------------------- DB helpers ----------------------
def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.executescript("""
    PRAGMA foreign_keys = ON;

    CREATE TABLE IF NOT EXISTS rcas (
        rca_id TEXT PRIMARY KEY,
        oem TEXT NOT NULL,
        environment TEXT NOT NULL CHECK(environment IN ('Pre-Live','UAT','Production')),
        system_component TEXT,
        severity TEXT,
        title TEXT NOT NULL,
        root_cause TEXT,
        created_by TEXT,
        created_at TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('Open','Closed','Reopened')) DEFAULT 'Open'
    );

    CREATE TABLE IF NOT EXISTS actions (
        action_id TEXT PRIMARY KEY,
        rca_id TEXT NOT NULL,
        action_text TEXT NOT NULL,
        action_type TEXT,
        owner_team TEXT,
        owner_person TEXT,
        due_date TEXT,
        status TEXT NOT NULL CHECK(status IN ('To Do','In Progress','Evidence Submitted','Verified','Closed')) DEFAULT 'To Do',
        verification_method TEXT,
        verified_by TEXT,
        verified_at TEXT,
        verification_notes TEXT,
        FOREIGN KEY (rca_id) REFERENCES rcas(rca_id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS evidence (
        evidence_id TEXT PRIMARY KEY,
        action_id TEXT NOT NULL,
        evidence_type TEXT NOT NULL CHECK(evidence_type IN ('Link','File note','Screenshot note','Test run note','Monitoring note')),
        evidence_ref TEXT NOT NULL,
        submitted_by TEXT,
        submitted_at TEXT NOT NULL,
        FOREIGN KEY (action_id) REFERENCES actions(action_id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS incidents (
        incident_id TEXT PRIMARY KEY,
        oem TEXT NOT NULL,
        environment TEXT NOT NULL CHECK(environment IN ('Pre-Live','UAT','Production')),
        system_component TEXT,
        severity TEXT,
        summary TEXT NOT NULL,
        created_at TEXT NOT NULL,
        linked_rca_id TEXT,
        FOREIGN KEY (linked_rca_id) REFERENCES rcas(rca_id) ON DELETE SET NULL
    );
    """)
    conn.commit()
    conn.close()

def qdf(sql, params=None):
    conn = get_conn()
    df = pd.read_sql_query(sql, conn, params=params or {})
    conn.close()
    return df

def exec_sql(sql, params=None):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(sql, params or {})
    conn.commit()
    conn.close()

def exec_many(sql, rows):
    conn = get_conn()
    cur = conn.cursor()
    cur.executemany(sql, rows)
    conn.commit()
    conn.close()

def gen_id(prefix):
    import random, string
    return f"{prefix}-" + "".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(7))

# ---------------------- AI-ish helpers ----------------------
def build_rca_corpus(rca_df):
    # Combine title + root_cause + actions (flatten)
    if rca_df.empty:
        return [], []
    actions = qdf("SELECT rca_id, action_text FROM actions")
    grouped = actions.groupby("rca_id")["action_text"].apply(lambda s: " | ".join(s.tolist())).to_dict()
    texts = []
    ids = []
    for _, r in rca_df.iterrows():
        ids.append(r["rca_id"])
        texts.append(" ".join([str(r.get("title","") or ""), str(r.get("root_cause","") or ""), grouped.get(r["rca_id"], "")]))
    return ids, texts

def top_similar_rcas(query_text, topk=5):
    rcas = qdf("SELECT rca_id, title, root_cause, oem, environment, created_at, status FROM rcas")
    ids, texts = build_rca_corpus(rcas)
    if not ids:
        return pd.DataFrame()
    vect = TfidfVectorizer(stop_words="english", ngram_range=(1,2), max_features=6000)
    X = vect.fit_transform(texts + [query_text])
    sims = cosine_similarity(X[-1], X[:-1]).flatten()
    rcas = rcas.copy()
    rcas["similarity"] = sims
    rcas = rcas.sort_values("similarity", ascending=False).head(topk)
    return rcas[["rca_id","title","oem","environment","created_at","status","similarity"]]

# ---------------------- UI ----------------------
st.set_page_config(page_title="RCA Closed-Loop Dashboard (MVP)", layout="wide")
init_db()

st.title("RCA Closed-Loop Dashboard (MVP)")
st.caption("Open-source demo: Streamlit + SQLite + TF‑IDF similarity. Focus: evidence + verification to prevent recurrence.")

# Sidebar filters
with st.sidebar:
    st.header("Filters")
    oem_filter = st.text_input("OEM contains", value="")
    env_filter = st.multiselect("Environment", ["Pre-Live","UAT","Production"], default=["Pre-Live","UAT","Production"])
    status_filter = st.multiselect("RCA Status", ["Open","Closed","Reopened"], default=["Open","Reopened","Closed"])
    show_last_6_months_prelive = st.checkbox("Pre-Live last 6 months (audit view)", value=False)
    st.divider()
    st.subheader("Quick actions")
    if st.button("Seed demo data"):
        from seed import seed_demo
        seed_demo(DB_PATH)
        st.success("Seeded demo data. Refreshing...")
        st.rerun()

# Data queries
params = {"oem_like": f"%{oem_filter.strip()}%"}
rca_sql = """
SELECT * FROM rcas
WHERE oem LIKE :oem_like
"""
rcas = qdf(rca_sql, params)

if env_filter:
    rcas = rcas[rcas["environment"].isin(env_filter)]
if status_filter:
    rcas = rcas[rcas["status"].isin(status_filter)]

if show_last_6_months_prelive:
    cutoff = (date.today() - timedelta(days=183)).isoformat()
    rcas = rcas[(rcas["environment"]=="Pre-Live") & (rcas["created_at"] >= cutoff)]

actions = qdf("SELECT * FROM actions")
evidence = qdf("SELECT * FROM evidence")
incidents = qdf("SELECT * FROM incidents")

# KPI calculations
def kpi_counts():
    if rcas.empty:
        return dict(open_actions=0, overdue=0, missing_evidence=0, verified_pct=0.0, evidenced_pct=0.0, recurrence_30=0)
    # actions tied to current filtered rcas
    a = actions[actions["rca_id"].isin(rcas["rca_id"])].copy()
    if a.empty:
        return dict(open_actions=0, overdue=0, missing_evidence=0, verified_pct=0.0, evidenced_pct=0.0, recurrence_30=0)
    open_actions = (a["status"].isin(["To Do","In Progress","Evidence Submitted"])).sum()
    # overdue
    today = date.today().isoformat()
    a_due = a.dropna(subset=["due_date"]).copy()
    overdue = ((a_due["due_date"] < today) & (a_due["status"].isin(["To Do","In Progress","Evidence Submitted"]))).sum()
    # missing evidence for actions that are not verified/closed
    ev_actions = set(evidence["action_id"].unique().tolist())
    missing_evidence = ((~a["action_id"].isin(ev_actions)) & (a["status"].isin(["To Do","In Progress","Evidence Submitted"]))).sum()
    verified_pct = float((a["status"].isin(["Verified","Closed"])).mean() * 100.0)
    evidenced_pct = float((a["action_id"].isin(ev_actions)).mean() * 100.0)
    # recurrence proxy: incidents last 30 days similar to prior RCAs and not linked OR linked to non-verified actions
    cutoff = (date.today() - timedelta(days=30)).isoformat()
    recent_inc = incidents[incidents["created_at"] >= cutoff].copy()
    recurrence_30 = len(recent_inc)
    return dict(open_actions=int(open_actions), overdue=int(overdue), missing_evidence=int(missing_evidence),
                verified_pct=verified_pct, evidenced_pct=evidenced_pct, recurrence_30=int(recurrence_30))

k = kpi_counts()
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Open actions", k["open_actions"])
c2.metric("Overdue actions", k["overdue"])
c3.metric("Missing evidence", k["missing_evidence"])
c4.metric("Evidenced %", f'{k["evidenced_pct"]:.0f}%')
c5.metric("Verified/Closed %", f'{k["verified_pct"]:.0f}%')

tab1, tab2, tab3, tab4, tab5 = st.tabs(["RCA Audit", "Action Tracker", "RCA Detail", "New Incident (AI match)", "Admin"])

with tab1:
    st.subheader("RCA Audit")
    st.write("Filter on **Pre-Live last 6 months** in the sidebar to replicate your audit request.")
    if rcas.empty:
        st.info("No RCAs match your filters.")
    else:
        # Add derived counts
        a = actions.groupby("rca_id").size().rename("actions_total")
        a_open = actions[actions["status"].isin(["To Do","In Progress","Evidence Submitted"])].groupby("rca_id").size().rename("actions_open")
        ev = evidence.groupby("action_id").size().rename("evidence_count")
        action_ev = actions[["action_id","rca_id"]].merge(ev, how="left", on="action_id").fillna({"evidence_count":0})
        ev_missing = action_ev[action_ev["evidence_count"]==0].groupby("rca_id").size().rename("actions_missing_evidence")
        view = rcas.merge(a, left_on="rca_id", right_index=True, how="left") \
                   .merge(a_open, left_on="rca_id", right_index=True, how="left") \
                   .merge(ev_missing, left_on="rca_id", right_index=True, how="left") \
                   .fillna({"actions_total":0,"actions_open":0,"actions_missing_evidence":0})
        view = view.sort_values(["environment","created_at"], ascending=[True, False])
        st.dataframe(view[["rca_id","oem","environment","system_component","severity","title","created_at","status","actions_total","actions_open","actions_missing_evidence"]],
                     use_container_width=True, hide_index=True)

with tab2:
    st.subheader("Action Tracker")
    if rcas.empty:
        st.info("No RCAs match your filters.")
    else:
        a = actions[actions["rca_id"].isin(rcas["rca_id"])].copy()
        if a.empty:
            st.info("No actions for current RCA selection.")
        else:
            # Add evidence present flag
            ev_actions = set(evidence["action_id"].unique().tolist())
            a["evidence_present"] = a["action_id"].isin(ev_actions)
            st.dataframe(a[["action_id","rca_id","action_text","owner_team","owner_person","due_date","status","evidence_present","verification_method","verified_by","verified_at"]],
                         use_container_width=True, hide_index=True)
            st.caption("Tip: keep the rule — **not done until Evidence Submitted + Verified**.")

with tab3:
    st.subheader("RCA Detail")
    if rcas.empty:
        st.info("No RCAs match your filters.")
    else:
        selected = st.selectbox("Select RCA", rcas["rca_id"].tolist(), format_func=lambda rid: f"{rid} — {rcas.set_index('rca_id').loc[rid,'title']}")
        r = rcas.set_index("rca_id").loc[selected].to_dict()
        left, right = st.columns([2,1])
        with left:
            st.markdown(f"### {r['title']}")
            st.write(f"**OEM:** {r['oem']}  |  **Env:** {r['environment']}  |  **System:** {r.get('system_component','')}  |  **Severity:** {r.get('severity','')}")
            st.write(f"**Created:** {r['created_at']}  |  **Status:** {r['status']}  |  **Created by:** {r.get('created_by','')}")
            st.markdown("**Root cause**")
            st.write(r.get("root_cause",""))
        with right:
            st.markdown("### Governance")
            st.write("✅ Evidence required")
            st.write("✅ Separate verification")
            st.write("✅ Audit trail ready (MVP)")

        st.divider()
        st.markdown("#### Remedial actions")
        a = actions[actions["rca_id"]==selected].copy()
        if a.empty:
            st.info("No actions recorded for this RCA yet.")
        else:
            ev_actions = set(evidence["action_id"].unique().tolist())
            a["evidence_present"] = a["action_id"].isin(ev_actions)
            st.dataframe(a[["action_id","action_text","action_type","owner_team","owner_person","due_date","status","evidence_present","verification_method","verified_by","verified_at"]],
                         use_container_width=True, hide_index=True)

        st.markdown("#### Evidence")
        ev = evidence.merge(actions[["action_id","rca_id","action_text"]], on="action_id", how="left")
        ev = ev[ev["rca_id"]==selected].copy()
        if ev.empty:
            st.info("No evidence uploaded/linked yet.")
        else:
            st.dataframe(ev[["evidence_id","action_id","evidence_type","evidence_ref","submitted_by","submitted_at"]],
                         use_container_width=True, hide_index=True)

with tab4:
    st.subheader("New Incident (AI match)")
    st.write("Paste a new incident summary. The MVP uses **TF‑IDF similarity** to suggest likely related RCAs (open-source).")
    inc_oem = st.text_input("OEM", value="Nissan")
    inc_env = st.selectbox("Environment", ["Production","UAT","Pre-Live"])
    inc_system = st.text_input("System / component", value="")
    inc_sev = st.selectbox("Severity", ["P1","P2","P3","P4"], index=1)
    inc_summary = st.text_area("Incident summary", height=120, placeholder="Describe the issue. Example: 'Same UAT timeout observed again in production during ...'")

    colA, colB = st.columns([1,1])
    with colA:
        if st.button("Find similar RCAs"):
            if not inc_summary.strip():
                st.warning("Please enter an incident summary.")
            else:
                sims = top_similar_rcas(f"{inc_oem} {inc_env} {inc_system} {inc_summary}")
                if sims.empty:
                    st.info("No RCAs found yet.")
                else:
                    st.dataframe(sims, use_container_width=True, hide_index=True)
                    st.caption("Use this to detect recurrence like the Nissan example and force re-verification when needed.")

    with colB:
        if st.button("Log incident"):
            if not inc_summary.strip():
                st.warning("Please enter an incident summary.")
            else:
                inc_id = gen_id("INC")
                now = date.today().isoformat()
                exec_sql("""
                    INSERT INTO incidents (incident_id,oem,environment,system_component,severity,summary,created_at,linked_rca_id)
                    VALUES (:incident_id,:oem,:environment,:system_component,:severity,:summary,:created_at,:linked_rca_id)
                """, dict(incident_id=inc_id,oem=inc_oem,environment=inc_env,system_component=inc_system,severity=inc_sev,
                          summary=inc_summary,created_at=now,linked_rca_id=None))
                st.success(f"Incident logged: {inc_id}")

with tab5:
    st.subheader("Admin")
    st.write("This tab lets you create RCAs/actions quickly for the demo.")
    with st.expander("Create new RCA", expanded=False):
        col1, col2, col3 = st.columns(3)
        with col1:
            oem = st.text_input("OEM*", value="")
            env = st.selectbox("Environment*", ["Pre-Live","UAT","Production"], index=0)
            sev = st.selectbox("Severity", ["P1","P2","P3","P4"], index=2)
        with col2:
            system_component = st.text_input("System / component", value="")
            created_by = st.text_input("Created by", value="")
            status = st.selectbox("Status", ["Open","Closed","Reopened"], index=0)
        with col3:
            title = st.text_input("Title*", value="")
            created_at = st.date_input("Created date", value=date.today()).isoformat()

        root_cause = st.text_area("Root cause", height=120)
        if st.button("Create RCA"):
            if not oem.strip() or not title.strip():
                st.warning("OEM and Title are required.")
            else:
                rid = gen_id("RCA")
                exec_sql("""
                    INSERT INTO rcas (rca_id,oem,environment,system_component,severity,title,root_cause,created_by,created_at,status)
                    VALUES (:rca_id,:oem,:environment,:system_component,:severity,:title,:root_cause,:created_by,:created_at,:status)
                """, dict(rca_id=rid,oem=oem,environment=env,system_component=system_component,severity=sev,title=title,
                          root_cause=root_cause,created_by=created_by,created_at=created_at,status=status))
                st.success(f"Created {rid}. Go to RCA Detail to add actions.")
                st.rerun()

    with st.expander("Add action to an RCA", expanded=False):
        rca_list = qdf("SELECT rca_id, title FROM rcas ORDER BY created_at DESC")
        if rca_list.empty:
            st.info("No RCAs yet.")
        else:
            rid = st.selectbox("RCA", rca_list["rca_id"].tolist(), format_func=lambda x: f"{x} — {rca_list.set_index('rca_id').loc[x,'title']}")
            col1, col2, col3 = st.columns(3)
            with col1:
                owner_team = st.text_input("Owner team", value="Tech")
                owner_person = st.text_input("Owner person", value="")
            with col2:
                action_type = st.selectbox("Action type", ["Prevent","Detect","Process","Code fix","Config","Test coverage"], index=3)
                due_date = st.date_input("Due date", value=date.today()+timedelta(days=14)).isoformat()
            with col3:
                status = st.selectbox("Action status", ["To Do","In Progress","Evidence Submitted","Verified","Closed"], index=0)
                verification_method = st.text_input("Verification method (required)", value="Regression test + monitoring evidence")

            action_text = st.text_area("Action text*", height=100)
            if st.button("Add action"):
                if not action_text.strip():
                    st.warning("Action text is required.")
                elif not verification_method.strip():
                    st.warning("Verification method is required.")
                else:
                    aid = gen_id("ACT")
                    exec_sql("""
                        INSERT INTO actions (action_id,rca_id,action_text,action_type,owner_team,owner_person,due_date,status,verification_method)
                        VALUES (:action_id,:rca_id,:action_text,:action_type,:owner_team,:owner_person,:due_date,:status,:verification_method)
                    """, dict(action_id=aid,rca_id=rid,action_text=action_text,action_type=action_type,
                              owner_team=owner_team,owner_person=owner_person,due_date=due_date,status=status,
                              verification_method=verification_method))
                    st.success(f"Added action {aid}")
                    st.rerun()

    with st.expander("Add evidence to an action", expanded=False):
        a_list = qdf("SELECT action_id, rca_id, action_text FROM actions ORDER BY due_date ASC")
        if a_list.empty:
            st.info("No actions yet.")
        else:
            aid = st.selectbox("Action", a_list["action_id"].tolist(), format_func=lambda x: f"{x} — {a_list.set_index('action_id').loc[x,'action_text'][:60]}")
            etype = st.selectbox("Evidence type", ["Link","File note","Screenshot note","Test run note","Monitoring note"])
            eref = st.text_input("Evidence reference (URL or note)", value="")
            submitted_by = st.text_input("Submitted by", value="")
            if st.button("Add evidence"):
                if not eref.strip():
                    st.warning("Evidence reference is required.")
                else:
                    evid = gen_id("EVD")
                    exec_sql("""
                        INSERT INTO evidence (evidence_id,action_id,evidence_type,evidence_ref,submitted_by,submitted_at)
                        VALUES (:evidence_id,:action_id,:evidence_type,:evidence_ref,:submitted_by,:submitted_at)
                    """, dict(evidence_id=evid,action_id=aid,evidence_type=etype,evidence_ref=eref,
                              submitted_by=submitted_by,submitted_at=date.today().isoformat()))
                    st.success(f"Added evidence {evid}")
                    st.rerun()

    with st.expander("Verify / close an action", expanded=False):
        a_list = qdf("SELECT action_id, action_text, status FROM actions ORDER BY due_date ASC")
        if a_list.empty:
            st.info("No actions yet.")
        else:
            aid = st.selectbox("Action to update", a_list["action_id"].tolist(),
                               format_func=lambda x: f"{x} — {a_list.set_index('action_id').loc[x,'status']} — {a_list.set_index('action_id').loc[x,'action_text'][:55]}")
            new_status = st.selectbox("New status", ["In Progress","Evidence Submitted","Verified","Closed"], index=2)
            verified_by = st.text_input("Verified by", value="")
            notes = st.text_area("Verification notes", height=80)
            if st.button("Update action status"):
                params = dict(status=new_status, verified_by=verified_by.strip() or None,
                              verified_at=date.today().isoformat() if new_status in ("Verified","Closed") else None,
                              notes=notes.strip() or None, action_id=aid)
                exec_sql("""
                    UPDATE actions
                    SET status=:status,
                        verified_by=COALESCE(:verified_by, verified_by),
                        verified_at=COALESCE(:verified_at, verified_at),
                        verification_notes=COALESCE(:notes, verification_notes)
                    WHERE action_id=:action_id
                """, params)
                st.success("Updated.")
                st.rerun()
