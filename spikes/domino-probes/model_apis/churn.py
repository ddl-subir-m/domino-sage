# Throwaway Model API #1 of 2 — the numeric one.
#
# Exists so #34's Model API half and #9's criterion 4 have something real to call. Deployed by
# spikes/domino-probes/create_two_model_apis.sh. Safe to delete once those close.
#
# Paired with model_priority.py ON PURPOSE: the two take DIFFERENT argument shapes. That is the
# case #35 raises — two Model APIs on one screen mean two input shapes — and it is the only way to
# see whether the built app really chose per call site rather than sending everything to the first
# Binding. A call wired to the wrong model here answers with the wrong keys, loudly.
#
# Stdlib only. A Model API build that has to resolve packages is a slower way to fail.
#
# Sample request body (this is what the Overview page's snippet carries):
#   {"data": {"tenure_months": 3, "monthly_spend": 129.5, "support_tickets": 4}}


def predict(tenure_months=12, monthly_spend=50.0, support_tickets=0):
    """Churn risk for one customer. Three numbers in, one banded verdict out.

    tenure_months   how long they have been a customer
    monthly_spend   their current monthly bill, in dollars
    support_tickets tickets they opened in the last 90 days
    """
    # Domino passes JSON through, and a browser fetch can easily send "3" where 3 was meant.
    # Cast rather than trust: a TypeError here reads as a 500 in the viewer's browser, which is
    # exactly the opaque failure #9's third criterion is about.
    tenure_months = float(tenure_months)
    monthly_spend = float(monthly_spend)
    support_tickets = float(support_tickets)

    # Deterministic and readable on purpose. You need to be able to tell a real answer from a
    # placeholder the agent invented, and "0.62" you can reproduce by hand does that.
    risk = 0.0
    drivers = []
    if tenure_months < 6:
        risk += 0.35
        drivers.append("new account")
    if monthly_spend > 100:
        risk += 0.20
        drivers.append("high spend")
    if support_tickets >= 3:
        risk += 0.30
        drivers.append("repeat support contact")
    risk = min(risk, 1.0)

    band = "high" if risk >= 0.6 else "medium" if risk >= 0.3 else "low"
    return {"risk": round(risk, 3), "band": band, "drivers": drivers}
