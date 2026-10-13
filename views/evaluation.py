"""Evaluation view: Benchmark performance against the 20 adversarial golden test cases."""
from __future__ import annotations

import streamlit as st
import pandas as pd

from eval.run_eval import load_golden_set, run_evaluation
from ui_kit import get_ctx


def render_evaluation() -> None:
    ctx = get_ctx()

    st.title("🧪 Evaluation Benchmark")
    st.caption("Benchmark the triage system against the golden adversarial evaluation suite (prompt injections, legal threats, multi-intent, edge cases).")

    golden_cases = load_golden_set()
    st.info(f"Loaded {len(golden_cases)} curated test cases from `eval/golden_set.json`.")

    col_btn1, col_btn2, _ = st.columns([1.5, 1.5, 3])
    run_deterministic = col_btn1.button("⚡ Run Deterministic / Rules Check", use_container_width=True)
    run_full_llm = col_btn2.button("🤖 Run Full Multi-Model LLM Benchmark", use_container_width=True, disabled=ctx.is_offline)

    if run_deterministic or run_full_llm:
        with st.spinner("Executing benchmark across evaluation cases..."):
            report = run_evaluation(golden_cases=golden_cases, run_llm=bool(run_full_llm))
            st.session_state["eval_report"] = report

    report = st.session_state.get("eval_report")
    if not report:
        st.write("Click one of the buttons above to run the benchmark suite.")
        with st.expander("Preview Golden Dataset"):
            st.dataframe(
                pd.DataFrame([
                    {
                        "ID": c["id"],
                        "Sender": c.get("sender"),
                        "Subject": c.get("subject"),
                        "Expected Status": c.get("expected_status"),
                        "Expected Escalate": c.get("expected_escalate"),
                    }
                    for c in golden_cases
                ]),
                use_container_width=True,
            )
        return

    # Metrics Display
    st.divider()
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Status Accuracy", f"{report['status_accuracy']:.1%}")
    m2.metric("Escalation Recall", f"{report['escalation_recall']:.1%}")
    m3.metric("Verifier Agreements", report["verifier_agreement_count"])
    total_tokens = report["classifier_tokens_total"] + report["verifier_tokens_total"]
    m4.metric("LLM Tokens Used", f"{total_tokens:,}")

    # Confusion matrix & details
    c_left, c_right = st.columns([1, 1.5])
    with c_left:
        st.subheader("Escalation Detection Confusion Matrix")
        conf = report["escalation_confusion"]
        cm_df = pd.DataFrame(
            [
                {"Actual": "Escalate (True)", "Predicted Escalate": conf["TP"], "Predicted Safe": conf["FN"]},
                {"Actual": "Safe (False)", "Predicted Escalate": conf["FP"], "Predicted Safe": conf["TN"]},
            ]
        ).set_index("Actual")
        st.table(cm_df)

    with c_right:
        st.subheader("Test Breakdown")
        results = report.get("case_results", [])
        breakdown_rows = []
        for r in results:
            correct_status = r["actual_status"] == r["expected_status"]
            correct_esc = r["actual_escalate"] == r["expected_escalate"]
            passed = correct_status and correct_esc
            breakdown_rows.append({
                "Status": "✅ PASS" if passed else "❌ FAIL",
                "ID": r["id"],
                "Expected": f"{r['expected_status']} (Esc: {r['expected_escalate']})",
                "Actual": f"{r['actual_status']} (Esc: {r['actual_escalate']})",
                "Subject": r.get("subject", ""),
            })

        st.dataframe(pd.DataFrame(breakdown_rows), use_container_width=True, height=260)


render_evaluation()
