# Throwaway Model API #2 of 2 — the text one.
#
# Exists so #34's Model API half and #9's criterion 4 have something real to call. Deployed by
# spikes/domino-probes/create_two_model_apis.sh. Safe to delete once those close.
#
# Its arguments deliberately share NO name with model_churn.py's. See that file's header for why.
#
# Stdlib only.
#
# Sample request body:
#   {"data": {"subject": "Cannot log in", "body": "urgent, whole team is blocked", "customer_tier": "enterprise"}}


_URGENT = ("urgent", "asap", "immediately", "blocked", "outage", "down", "critical")
_BILLING = ("invoice", "billing", "charge", "refund", "payment")


def predict(subject="", body="", customer_tier="standard"):
    """Triage one support ticket. Two strings and a tier in, one priority out.

    subject       the ticket's subject line
    body          the ticket's message text
    customer_tier one of: enterprise, business, standard
    """
    text = f"{subject} {body}".lower()

    score = 0
    matched = []
    for word in _URGENT:
        if word in text:
            score += 2
            matched.append(word)
    for word in _BILLING:
        if word in text:
            score += 1
            matched.append(word)

    # Tier is a multiplier, not a keyword, so the same text answers differently per customer. That
    # gives you a second way to prove the call really reached this model and carried all three args.
    score += {"enterprise": 3, "business": 1}.get(str(customer_tier).lower(), 0)

    priority = "P1" if score >= 5 else "P2" if score >= 2 else "P3"
    return {"priority": priority, "score": score, "matched": matched}
